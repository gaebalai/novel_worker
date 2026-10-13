#!/usr/bin/env node
// Anthropic Messages API를 사용하는 전단 프록시 (novel_worker 판).
// model 이름으로 프로바이더를 결정하고, OpenAI 호환 API로 상호 변환한다.
// 어느 프로바이더에도 일치하지 않는 model(claude-*)은 upstream (api.anthropic.com)으로 무수정 중계한다.
// 바탕: ~/.claude/model-bridge/proxy.mjs. 차이: GPT·GLM·Qwen 추가, 업체별 tokenParam·effortParam·streamUsage·dropTools,
// 기본 포트 8788, ANTHROPIC_API_KEY 보충, 상위 호출에 타임아웃 없음(upstreamFetch), Host·Content-Type 검사.

import http from "node:http";
import https from "node:https";
import { Readable } from "node:stream";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const UA = "novel_worker-bridge/1.1 (+https://github.com/gaebalai/novel_worker)";
const DIR = path.dirname(fs.realpathSync(fileURLToPath(import.meta.url)));
const PID_FILE = process.env.BRIDGE_PID_FILE || path.join(DIR, "bridge.pid"); // 회귀 테스트는 다른 곳을 쓴다(운영 중계기의 pid 파일 보호)

// OpenAI 호환 프로바이더. 위에서부터 순서대로 model 이름을 대조한다.
// 추가할 때는 이 배열에 항목 1개를 더한다.
const PROVIDERS = [
  {
    name: "deepseek",
    re: /^deepseek/i,
    chatUrl: process.env.DEEPSEEK_BASE_URL || "https://api.deepseek.com/chat/completions",
    keyEnv: "DEEPSEEK_API_KEY",
    images: "drop", // vision 미지원
  },
  {
    name: "kimi",
    re: /^(kimi|moonshot)/i,
    chatUrl: process.env.KIMI_BASE_URL || "https://api.moonshot.ai/v1/chat/completions",
    keyEnv: "KIMI_API_KEY",
    images: "url", // vision 지원
  },
  {
    name: "openai",
    re: /^(gpt|o\d|chatgpt)/i,
    chatUrl: process.env.OPENAI_BASE_URL || "https://api.openai.com/v1/chat/completions",
    keyEnv: "OPENAI_API_KEY",
    images: "url",
    tokenParam: "max_completion_tokens", // 추론형 GPT는 max_tokens를 거부한다
    // gpt-6.1-sol 은 chat/completions 에서 tool calling 을 지원하지 않는다(2026-10-14 공식 모델 문서 확인).
    // 도구 목록을 빼고 보내 텍스트로만 답하게 한다. 도구가 필요한 일은 다른 모델로.
    dropTools: process.env.OPENAI_DROP_TOOLS !== "0",
    effortParam: "reasoning_effort", // output_config.effort → reasoning_effort
  },
  {
    name: "glm",
    re: /^glm/i,
    chatUrl: process.env.ZAI_BASE_URL || "https://api.z.ai/api/paas/v4/chat/completions",
    keyEnv: "ZAI_API_KEY",
    images: "drop",
    streamUsage: false, // z.ai 문서에 stream_options 가 없다(2026-10-14 확인)
  },
  {
    name: "qwen",
    re: /^(qwen|qwq)/i,
    chatUrl: process.env.DASHSCOPE_BASE_URL || "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
    keyEnv: "DASHSCOPE_API_KEY",
    images: "url",
  },
];

const CFG = {
  port: Number(process.env.BRIDGE_PORT || 8788),
  host: process.env.BRIDGE_HOST || "127.0.0.1",
  upstream: (process.env.BRIDGE_UPSTREAM || "https://api.anthropic.com").replace(/\/$/, ""),
  maxTokens: Number(process.env.BRIDGE_MAX_TOKENS || 65536),
  emitThinking: process.env.BRIDGE_EMIT_THINKING !== "0",
  // finish_reason 없이 상위가 끝나면 에러로 처리한다 (조용한 잘림을 completion으로 오인시키지 않는다)
  strictFinish: process.env.BRIDGE_STRICT_FINISH !== "0",
  logFile: process.env.BRIDGE_LOG || path.join(DIR, "bridge.log"),
  debug: process.env.BRIDGE_DEBUG === "1",
};

function log(line) {
  const s = `${new Date().toISOString()} ${line}\n`;
  try { fs.appendFileSync(CFG.logFile, s); } catch {}
  if (CFG.debug) process.stderr.write(s);
}

function providerFor(model) {
  return PROVIDERS.find((p) => p.re.test(model || "")) || null;
}

// ---------------------------------------------------------------- utilities

function estimateTokens(x) {
  const s = typeof x === "string" ? x : JSON.stringify(x ?? "");
  return Math.max(1, Math.ceil(s.length / 3.5));
}

// Claude Code는 문맥 길이를 나타내는 [1m] 같은 접미사를 붙일 때가 있다. 상위로는 넘기지 않는다.
function baseModelName(model) {
  return String(model || "").replace(/\[[^\]]*\]\s*$/, "");
}

// OpenAI의 function name은 ^[A-Za-z0-9_-]{1,64}$로 제한된다.
function sanitizeToolName(name, used) {
  let s = String(name).replace(/[^A-Za-z0-9_-]/g, "_").slice(0, 64);
  if (!s) s = "tool";
  let out = s, i = 2;
  while (used.has(out)) { out = `${s.slice(0, 60)}_${i++}`; }
  used.add(out);
  return out;
}

function buildToolMaps(tools) {
  const used = new Set(), fwd = new Map(), rev = new Map();
  for (const t of tools || []) {
    const san = sanitizeToolName(t.name, used);
    fwd.set(t.name, san);
    rev.set(san, t.name);
  }
  return { fwd, rev };
}

function stripSchema(schema) {
  if (!schema || typeof schema !== "object") return { type: "object", properties: {} };
  const { $schema, ...rest } = schema;
  return rest;
}

function textOfBlocks(blocks) {
  if (typeof blocks === "string") return blocks;
  if (!Array.isArray(blocks)) return "";
  const parts = [];
  for (const b of blocks) {
    if (typeof b === "string") { parts.push(b); continue; }
    if (b && b.type === "text" && typeof b.text === "string") parts.push(b.text);
  }
  return parts.join("\n");
}

// ------------------------------------------------- Anthropic -> OpenAI (req)

function anthropicToOpenAI(body, prov) {
  const { fwd, rev } = buildToolMaps(body.tools);
  const messages = [];

  const sysText = textOfBlocks(body.system);
  if (sysText && sysText.trim()) messages.push({ role: "system", content: sysText });

  for (const m of body.messages || []) {
    const role = m.role;
    const content = m.content;

    if (typeof content === "string") {
      if (content) messages.push({ role, content });
      continue;
    }
    if (!Array.isArray(content)) continue;

    if (role === "user") {
      const toolMsgs = [], parts = [];
      for (const b of content) {
        if (!b || typeof b !== "object") continue;
        if (b.type === "tool_result") {
          let c = typeof b.content === "string" ? b.content : textOfBlocks(b.content);
          if (b.is_error) c = `ERROR: ${c}`;
          toolMsgs.push({ role: "tool", tool_call_id: b.tool_use_id, content: c || "(empty)" });
        } else if (b.type === "text") {
          parts.push(b.text || "");
        } else if (b.type === "image") {
          if (prov.images === "url" && b.source?.type === "base64") {
            parts.push({ __image: `data:${b.source.media_type};base64,${b.source.data}` });
          } else {
            parts.push("[image omitted]");
          }
        }
      }
      // tool 응답은 대응하는 assistant tool_calls 바로 뒤에 둬야 한다.
      for (const tm of toolMsgs) messages.push(tm);
      const imgs = parts.filter((p) => p && p.__image);
      const txt = parts.filter((p) => typeof p === "string").join("\n");
      if (imgs.length) {
        const arr = [];
        if (txt) arr.push({ type: "text", text: txt });
        for (const im of imgs) arr.push({ type: "image_url", image_url: { url: im.__image } });
        messages.push({ role: "user", content: arr });
      } else if (txt.trim()) {
        messages.push({ role: "user", content: txt });
      }
      continue;
    }

    if (role === "assistant") {
      const texts = [], toolCalls = [];
      for (const b of content) {
        if (!b || typeof b !== "object") continue;
        if (b.type === "text") texts.push(b.text || "");
        else if (b.type === "tool_use") {
          toolCalls.push({
            id: b.id, type: "function",
            function: { name: fwd.get(b.name) || b.name, arguments: JSON.stringify(b.input ?? {}) },
          });
        }
        // thinking / redacted_thinking은 버린다.
      }
      const msg = { role: "assistant", content: texts.join("\n") };
      if (toolCalls.length) msg.tool_calls = toolCalls;
      if (msg.content || toolCalls.length) messages.push(msg);
      continue;
    }
  }

  const payload = { model: baseModelName(body.model), messages, stream: !!body.stream };
  const mt = Number(body.max_tokens || 0);
  payload[prov.tokenParam || "max_tokens"] = Math.min(mt > 0 ? mt : CFG.maxTokens, CFG.maxTokens);
  if (typeof body.temperature === "number") payload.temperature = body.temperature;
  if (typeof body.top_p === "number") payload.top_p = body.top_p;
  if (Array.isArray(body.stop_sequences) && body.stop_sequences.length) payload.stop = body.stop_sequences.slice(0, 4);
  if (payload.stream && prov.streamUsage !== false) payload.stream_options = { include_usage: true };
  const effort = body.output_config?.effort;
  if (prov.effortParam && typeof effort === "string") payload[prov.effortParam] = effort;

  if (Array.isArray(body.tools) && body.tools.length && !prov.dropTools) {
    payload.tools = body.tools.filter((t) => t && t.name).map((t) => ({
      type: "function",
      function: {
        name: fwd.get(t.name) || t.name,
        description: (t.description || "").slice(0, 8192),
        parameters: stripSchema(t.input_schema),
      },
    }));
    const tc = body.tool_choice;
    if (tc?.type === "any") payload.tool_choice = "required";
    else if (tc?.type === "tool" && tc.name) payload.tool_choice = { type: "function", function: { name: fwd.get(tc.name) || tc.name } };
    else if (tc?.type === "none") payload.tool_choice = "none";
    else payload.tool_choice = "auto";
  }

  return { payload, rev };
}

// ------------------------------------------------ OpenAI -> Anthropic (resp)

const STOP_MAP = { stop: "end_turn", tool_calls: "tool_use", length: "max_tokens", content_filter: "end_turn" };

// tool_calls를 실어 보내면서 finish_reason은 stop으로 주는 프로바이더가 있다.
// 그대로 매핑하면 tool_use 블록이 들어 있는데 stop_reason=end_turn인 모순된 응답이 되어,
// 호출한 쪽이 툴을 실행하지 않고 턴을 끝낼 수 있다. 실제로 내보낸 블록을 기준으로 정정한다.
// 단 length(=max_tokens)는 잘렸다는 사실 자체를 알려야 하므로 덮어쓰지 않는다.
function stopReasonFor(finishReason, hasToolUse) {
  const mapped = STOP_MAP[finishReason] || "end_turn";
  return hasToolUse && mapped === "end_turn" ? "tool_use" : mapped;
}

function usageOut(u, inEst) {
  return {
    input_tokens: u?.prompt_tokens ?? inEst,
    output_tokens: u?.completion_tokens ?? 0,
    cache_read_input_tokens: u?.prompt_cache_hit_tokens ?? 0,
    cache_creation_input_tokens: 0,
  };
}

function parseArgs(raw, where) {
  if (!raw) return {};
  try { return JSON.parse(raw); }
  catch { log(`bridge WARN malformed tool arguments (${where}), substituting {}`); return {}; }
}

function openAIToAnthropic(resp, model, rev, inEst) {
  const choice = resp.choices?.[0] || {};
  const msg = choice.message || {};
  const content = [];
  if (CFG.emitThinking && msg.reasoning_content) {
    content.push({ type: "thinking", thinking: msg.reasoning_content, signature: "" });
  }
  if (msg.content) content.push({ type: "text", text: msg.content });
  for (const tc of msg.tool_calls || []) {
    content.push({
      type: "tool_use",
      id: tc.id || `toolu_${Math.random().toString(36).slice(2)}`,
      name: rev.get(tc.function?.name) || tc.function?.name,
      input: parseArgs(tc.function?.arguments, "non-stream"),
    });
  }
  const hasToolUse = content.some((b) => b.type === "tool_use");
  if (!content.length) content.push({ type: "text", text: "" });
  return {
    id: `msg_${resp.id || Math.random().toString(36).slice(2)}`,
    type: "message", role: "assistant", model, content,
    stop_reason: stopReasonFor(choice.finish_reason, hasToolUse),
    stop_sequence: null,
    usage: usageOut(resp.usage, inEst),
  };
}

// Anthropic의 SSE 이벤트 열을 조립한다.
class SSEWriter {
  constructor(res, model, inEst) {
    this.res = res; this.model = model; this.inEst = inEst;
    this.index = -1; this.open = null;
    this.emittedTool = false; // stop_reason 정정용. flushTools에서 세운다.
  }
  send(event, data) { this.res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`); }
  start() {
    this.send("message_start", {
      type: "message_start",
      message: {
        id: `msg_${Math.random().toString(36).slice(2)}`,
        type: "message", role: "assistant", model: this.model,
        content: [], stop_reason: null, stop_sequence: null,
        usage: { input_tokens: this.inEst, output_tokens: 0, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 },
      },
    });
  }
  closeBlock() {
    if (this.open === null) return;
    if (this.open === "thinking") {
      this.send("content_block_delta", { type: "content_block_delta", index: this.index, delta: { type: "signature_delta", signature: "" } });
    }
    this.send("content_block_stop", { type: "content_block_stop", index: this.index });
    this.open = null;
  }
  openBlock(kind, block) {
    this.closeBlock();
    this.index += 1;
    this.open = kind;
    this.send("content_block_start", { type: "content_block_start", index: this.index, content_block: block });
    return this.index;
  }
  thinkingDelta(t) {
    if (this.open !== "thinking") this.openBlock("thinking", { type: "thinking", thinking: "" });
    this.send("content_block_delta", { type: "content_block_delta", index: this.index, delta: { type: "thinking_delta", thinking: t } });
  }
  textDelta(t) {
    if (this.open !== "text") this.openBlock("text", { type: "text", text: "" });
    this.send("content_block_delta", { type: "content_block_delta", index: this.index, delta: { type: "text_delta", text: t } });
  }
  // tool_use는 이름도 인자도 분할·순서 무관하게 도착할 수 있으므로, 전체 청크 수신 후 한꺼번에 내보낸다.
  flushTools(slots, rev) {
    for (const slot of [...slots.keys()].sort((a, b) => a - b)) {
      const s = slots.get(slot);
      if (!s || !s.name) continue;
      this.emittedTool = true;
      const input = parseArgs(s.args, `slot ${slot}`);
      const idx = this.openBlock("tool", {
        type: "tool_use",
        id: s.id || `toolu_${Math.random().toString(36).slice(2)}`,
        name: rev.get(s.name) || s.name,
        input: {},
      });
      this.send("content_block_delta", { type: "content_block_delta", index: idx, delta: { type: "input_json_delta", partial_json: JSON.stringify(input) } });
    }
  }
  finish(finishReason, usage) {
    this.closeBlock();
    this.send("message_delta", {
      type: "message_delta",
      delta: { stop_reason: stopReasonFor(finishReason, this.emittedTool), stop_sequence: null },
      usage: usageOut(usage, this.inEst),
    });
    this.send("message_stop", { type: "message_stop" });
  }
  // SSE 시작 후에 실패한 경우에는, 원시 JSON이 아니라 error 이벤트로 알린다.
  error(message) {
    this.closeBlock();
    this.send("error", { type: "error", error: { type: "api_error", message } });
  }
}

async function streamUpstream(upRes, res, model, rev, inEst) {
  res.writeHead(200, {
    "content-type": "text/event-stream; charset=utf-8",
    "cache-control": "no-cache",
    connection: "keep-alive",
  });
  const w = new SSEWriter(res, model, inEst);
  w.start();

  let finishReason = null, usage = null;
  const slots = new Map(); // index -> {id, name, args}

  try {
    const reader = upRes.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line.startsWith("data:")) continue;
        const payload = line.slice(5).trim();
        if (payload === "[DONE]") continue;
        let ev;
        try { ev = JSON.parse(payload); } catch { continue; }
        if (ev.usage) usage = ev.usage;
        const ch = ev.choices?.[0];
        if (!ch) continue;
        if (ch.finish_reason) finishReason = ch.finish_reason;
        const d = ch.delta || {};
        if (CFG.emitThinking && typeof d.reasoning_content === "string" && d.reasoning_content) w.thinkingDelta(d.reasoning_content);
        if (typeof d.content === "string" && d.content) w.textDelta(d.content);
        for (const tc of d.tool_calls || []) {
          const slot = tc.index ?? 0;
          let s = slots.get(slot);
          if (!s) { s = { id: null, name: "", args: "" }; slots.set(slot, s); }
          if (tc.id) s.id = tc.id;
          if (tc.function?.name) s.name += tc.function.name;
          if (tc.function?.arguments) s.args += tc.function.arguments;
        }
      }
    }
  } catch (e) {
    log(`bridge STREAM ABORTED model=${model} ${e?.message || e}`);
    w.error(`upstream stream failed: ${e?.message || e}`);
    return res.end();
  }

  if (finishReason === null && CFG.strictFinish) {
    log(`bridge STREAM TRUNCATED model=${model} (no finish_reason)`);
    w.error("upstream ended without finish_reason (response may be truncated)");
    return res.end();
  }

  w.flushTools(slots, rev);
  w.finish(finishReason ?? "stop", usage);
  res.end();
}

// ------------------------------------------------------------------ routing

// Node 내장 fetch(undici)는 응답 헤더를 300초까지만 기다린다. 비스트리밍 요청은 생성이 끝나야 헤더가 오므로
// 긴 집필이 "fetch failed"로 끊긴다. node:http(s) 로 직접 보내 타임아웃을 두지 않는다(마감은 호출하는 쪽이 관리).
// 반환값은 표준 Response 라서 .ok/.status/.text()/.json()/.body.getReader()/headers.forEach 를 그대로 쓴다.
function upstreamFetch(url, init = {}) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const lib = u.protocol === "https:" ? https : http;
    const headers0 = { ...(init.headers || {}) };
    if (init.body) headers0["content-length"] = String(Buffer.byteLength(init.body));
    // node:http 는 User-Agent 를 붙이지 않는다. 업체 앞의 Cloudflare 는 이름표 없는 요청을 막을 수 있다(error code 1010).
    // passthrough 는 클라이언트(Claude Code·엔진)의 User-Agent 를 그대로 넘기고, 없을 때만 중계기 이름을 붙인다.
    if (!Object.keys(headers0).some((k) => k.toLowerCase() === "user-agent")) headers0["user-agent"] = UA;
    const req = lib.request(u, { method: init.method || "GET", headers: headers0 }, (res) => {
      const headers = new Headers();
      for (const [k, v] of Object.entries(res.headers)) {
        if (Array.isArray(v)) v.forEach((x) => headers.append(k, x));
        else if (v !== undefined) headers.set(k, v);
      }
      const noBody = [204, 205, 304].includes(res.statusCode) || init.method === "HEAD";
      if (noBody) res.resume();
      resolve(new Response(noBody ? null : Readable.toWeb(res), { status: res.statusCode, headers }));
    });
    req.setTimeout(0);
    req.on("error", reject);
    // 호출한 쪽(엔진·Claude Code)이 끊으면 업체 요청도 끊는다. 그대로 두면 업체는 끝까지 생성하고 과금한다
    // (엔진이 마감으로 끊고 다시 보낼 때 이전 요청이 계속 돌아 비용이 겹친다).
    if (init.client) {
      init.client.on("close", () => {
        if (!init.client.writableEnded) {
          log(`bridge client closed early → upstream aborted ${u.host}${u.pathname}`);
          req.destroy(new Error("client closed"));
        }
      });
    }
    if (init.body) req.write(init.body);
    req.end();
  });
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

const HOP = new Set(["host", "content-length", "connection", "transfer-encoding", "accept-encoding", "keep-alive"]);

async function passthrough(req, res, raw, model) {
  const t0 = Date.now();
  const headers = {};
  for (const [k, v] of Object.entries(req.headers)) {
    if (!HOP.has(k.toLowerCase()) && typeof v === "string") headers[k] = v;
  }
  // 인증 헤더가 없는 요청(엔진이 키 없이 보낸 경우 등)에는 bridge 프로세스의 ANTHROPIC_API_KEY를 채운다.
  // Claude Code 세션은 자기 OAuth/키 헤더를 보내므로 건드리지 않는다.
  const hasAuth = Object.keys(headers).some((k) => ["x-api-key", "authorization"].includes(k.toLowerCase()));
  if (!hasAuth && process.env.ANTHROPIC_API_KEY) headers["x-api-key"] = process.env.ANTHROPIC_API_KEY;
  const init = { method: req.method, headers };
  if (raw && raw.length) init.body = raw;
  const up = await upstreamFetch(CFG.upstream + req.url, { ...init, client: res });
  const outHeaders = {};
  up.headers.forEach((v, k) => {
    const lk = k.toLowerCase();
    if (["content-encoding", "content-length", "transfer-encoding", "connection"].includes(lk)) return;
    outHeaders[k] = v;
  });
  res.writeHead(up.status, outHeaders);
  if (up.body) {
    const reader = up.body.getReader();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      res.write(Buffer.from(value));
    }
  }
  res.end();
  if (model) log(`anthropic passthrough model=${model} ${up.status} ${Date.now() - t0}ms`);
}

async function handleProvider(res, body, prov, t0) {
  const key = process.env[prov.keyEnv] || "";
  if (!key) {
    res.writeHead(401, { "content-type": "application/json" });
    return res.end(JSON.stringify({ type: "error", error: { type: "authentication_error", message: `${prov.keyEnv} is not set for the bridge process` } }));
  }
  const { payload, rev } = anthropicToOpenAI(body, prov);
  const inEst = estimateTokens(payload.messages);
  const upRes = await upstreamFetch(prov.chatUrl, {
    method: "POST",
    headers: { authorization: `Bearer ${key}`, "content-type": "application/json" },
    body: JSON.stringify(payload),
    client: res,
  });

  if (!upRes.ok) {
    const txt = await upRes.text();
    log(`${prov.name} ERROR ${upRes.status} model=${payload.model} ${txt.slice(0, 400)}`);
    res.writeHead(upRes.status, { "content-type": "application/json" });
    return res.end(JSON.stringify({ type: "error", error: { type: "api_error", message: `${prov.name} ${upRes.status}: ${txt.slice(0, 800)}` } }));
  }

  if (payload.stream) {
    await streamUpstream(upRes, res, body.model, rev, inEst);
    log(`${prov.name} stream ok model=${payload.model} tools=${payload.tools?.length ?? 0} ${Date.now() - t0}ms`);
    return;
  }
  const j = await upRes.json();
  res.writeHead(200, { "content-type": "application/json" });
  res.end(JSON.stringify(openAIToAnthropic(j, body.model, rev, inEst)));
  log(`${prov.name} json ok model=${payload.model} ${Date.now() - t0}ms`);
}

const server = http.createServer(async (req, res) => {
  const t0 = Date.now();
  try {
    if (req.url === "/__bridge/health") {
      res.writeHead(200, { "content-type": "application/json" });
      return res.end(JSON.stringify({
        ok: true, upstream: CFG.upstream,
        // anthropic 은 PROVIDERS 밖(그대로 통과)이지만, 엔진·기획 도구가 모드를 고를 수 있게 키 유무를 함께 알린다
        providers: [{ name: "anthropic", match: "claude-* (passthrough)", hasKey: !!process.env.ANTHROPIC_API_KEY }]
          .concat(PROVIDERS.map((p) => ({ name: p.name, match: String(p.re), hasKey: !!process.env[p.keyEnv] }))),
      }));
    }

    // 다른 사이트가 브라우저로 이 중계기를 부르는 것을 막는다(과금 호출·DNS rebinding).
    const host = String(req.headers.host || "").toLowerCase();
    if (![`127.0.0.1:${CFG.port}`, `localhost:${CFG.port}`].includes(host)) {
      res.writeHead(403, { "content-type": "application/json" });
      return res.end(JSON.stringify({ type: "error", error: { type: "permission_error", message: `bridge: Host ${host} 는 허용되지 않습니다` } }));
    }
    // 브라우저가 보낸 요청(Origin 이 붙음)과 CORS 사전 요청(OPTIONS)은 받지 않는다. Claude Code·엔진은 Origin 을 보내지 않는다.
    if (req.headers.origin || req.method === "OPTIONS") {
      res.writeHead(403, { "content-type": "application/json" });
      return res.end(JSON.stringify({ type: "error", error: { type: "permission_error", message: "bridge: 브라우저 요청(Origin/OPTIONS)은 받지 않습니다" } }));
    }
    if (req.method === "POST" && !/^application\/json\b/i.test(String(req.headers["content-type"] || ""))) {
      res.writeHead(415, { "content-type": "application/json" });
      return res.end(JSON.stringify({ type: "error", error: { type: "invalid_request_error", message: "bridge: content-type 은 application/json 이어야 합니다" } }));
    }

    const isMessages = req.method === "POST" && /^\/v1\/messages(\?|$)/.test(req.url);
    const isCount = req.method === "POST" && /^\/v1\/messages\/count_tokens/.test(req.url);
    if (!isMessages && !isCount) return await passthrough(req, res, await readBody(req), null);

    const raw = await readBody(req);
    let body;
    try { body = JSON.parse(raw.toString("utf8")); } catch { return await passthrough(req, res, raw, null); }

    const prov = providerFor(body.model);
    if (!prov) return await passthrough(req, res, raw, body.model || "unknown");

    if (isCount) {
      res.writeHead(200, { "content-type": "application/json" });
      return res.end(JSON.stringify({ input_tokens: estimateTokens({ s: body.system, m: body.messages, t: body.tools }) }));
    }
    await handleProvider(res, body, prov, t0);
  } catch (e) {
    log(`bridge EXCEPTION ${req.method} ${req.url} ${e?.stack || e}`);
    // 헤더 전송 후에 원시 JSON을 쓰면 SSE 본문이 깨지므로, 경로를 나눈다.
    const err = { type: "error", error: { type: "api_error", message: String(e?.message || e) } };
    if (!res.headersSent) {
      res.writeHead(500, { "content-type": "application/json" });
      try { res.end(JSON.stringify(err)); } catch {}
    } else {
      try { res.write(`event: error\ndata: ${JSON.stringify(err)}\n\n`); } catch {}
      try { res.end(); } catch {}
    }
  }
});

process.on("uncaughtException", (e) => log(`bridge uncaughtException ${e?.stack || e}`));
process.on("unhandledRejection", (e) => log(`bridge unhandledRejection ${e?.stack || e}`));

function removePid() {
  try { fs.unlinkSync(PID_FILE); } catch {}
}

// listen 실패 (EADDRINUSE 등) 는 여기서 끝낸다. 정리 훅을 걸기 전에 죽으므로
// 먼저 떠 있는 프로세스의 bridge.pid 를 건드리지 않는다.
server.on("error", (e) => {
  log(`bridge listen failed on ${CFG.host}:${CFG.port} ${e?.message || e}`);
  if (CFG.debug) process.stderr.write(`bridge listen failed: ${e?.message || e}\n`);
  process.exit(1);
});

server.headersTimeout = 0;
server.requestTimeout = 0;
server.listen(CFG.port, CFG.host, () => {
  try { fs.writeFileSync(PID_FILE, String(process.pid)); } catch {}
  // 정상 listen 후에만 정리 훅을 건다. 남은 pid 파일을 다음 실행이 kill 대상으로 삼으면
  // 그 사이 같은 pid 를 받은 무관한 프로세스를 죽일 수 있다.
  process.on("exit", removePid);
  for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"]) {
    process.on(sig, () => { removePid(); process.exit(0); });
  }
  const keys = PROVIDERS.map((p) => `${p.name}=${process.env[p.keyEnv] ? "yes" : "NO"}`).join(" ");
  log(`bridge listening on http://${CFG.host}:${CFG.port} upstream=${CFG.upstream} keys[${keys}]`);
  if (CFG.debug) process.stderr.write(`bridge up on ${CFG.host}:${CFG.port}\n`);
});
