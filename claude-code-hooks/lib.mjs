// 共用工具：状态文件、spans 落盘、stdin 解析。零依赖（node 内建）。
// 纪律与 src/telemetry.ts 相同：hook 绝不打断会话——所有错误吞掉、exit 0。
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

/** 状态/产物目录（主会话一个状态文件 + 每子代理一个 + 共享 spans.jsonl）。 */
export function obsDir() {
  return process.env.DBDOG_OBS_DIR?.trim() || path.join(os.homedir(), ".claude", "dbdog-obs");
}

/** agent_id 直接进文件名，先滤掉路径分隔符等（实测是 hex 串，属兜底）。 */
function safeAgentId(agentId) {
  return String(agentId).replace(/[^A-Za-z0-9_-]/g, "");
}

/**
 * 状态文件路径。带 agentId 时指向该子代理的独立状态。
 * 2026-08-09 拆分：并行子代理会同时触发 SubagentStop，共用一个状态文件必然
 * 读-改-写互相覆盖（实测两个子代理的 SubagentStop 间隔 692ms，而上报超时 3s，
 * 窗口必然重叠）。一子代理一文件 = 单写者，从根上没有竞态。
 */
export function statePath(sessionId, agentId) {
  const name = agentId ? `${sessionId}.${safeAgentId(agentId)}` : sessionId;
  return path.join(obsDir(), `${name}.json`);
}

/**
 * 确定性派生 span_id（16 hex）——与 root_span_id 从 trace_id 前 16 hex 派生同源。
 * 用途：子代理的 span 在 SubagentStop 时就要落盘，而父侧那次 Agent 调用的
 * tool_result（携带 agentId）此刻还没写进主 transcript，当场拿不到父 span_id。
 * 两侧各自用 (trace_id, agent_id) 算出同一个值，就不需要互相通信也能挂上父子。
 */
export function deriveSpanId(traceId, key) {
  return crypto.createHash("sha256").update(`${traceId}:${key}`).digest("hex").slice(0, 16);
}

/** spans 输出（Phase A 本地 JSONL；Phase C 起改 POST 上报，见 ADR-0008/课题 §5）。 */
export function spansPath() {
  return process.env.DBDOG_OBS_SPANS?.trim() || path.join(obsDir(), "spans.jsonl");
}

export function readState(sessionId, agentId) {
  try {
    return JSON.parse(fs.readFileSync(statePath(sessionId, agentId), "utf8"));
  } catch {
    return null;
  }
}

export function writeState(sessionId, state, agentId) {
  fs.mkdirSync(obsDir(), { recursive: true });
  fs.writeFileSync(statePath(sessionId, agentId), JSON.stringify(state));
}

export function appendSpans(spans) {
  if (!spans.length) return;
  fs.mkdirSync(path.dirname(spansPath()), { recursive: true });
  fs.appendFileSync(spansPath(), spans.map((s) => JSON.stringify(s)).join("\n") + "\n");
}

/**
 * span_id → span 全文的索引，读自本地 spans.jsonl（真相源）。
 * 状态文件里的 pending 只存 span_id、不存副本——旧格式直接塞全文，实测把单个状态
 * 文件撑到 315 KB（每条 span 的 input/output 上限 8000 字符）。
 */
export function spanIndex() {
  const index = new Map();
  let text;
  try {
    text = fs.readFileSync(spansPath(), "utf8");
  } catch {
    return index; // 没有本地 JSONL 就捞不回来
  }
  for (const line of text.split("\n")) {
    if (!line) continue;
    try {
      const span = JSON.parse(line);
      if (span?.span_id) index.set(span.span_id, span);
    } catch {
      /* 容忍脏行 */
    }
  }
  return index;
}

/** 按 span_id 捞回全文，保持传入顺序；捞不到的丢弃（本地 JSONL 已被轮转/删除）。 */
export function lookupSpans(ids) {
  if (!ids?.length) return [];
  const index = spanIndex();
  return ids.map((id) => index.get(id)).filter(Boolean);
}

/** 兼容两种 pending 格式：新的字符串 id、旧的 span 全文对象。统一成 id 列表。 */
export function pendingIds(pending) {
  if (!Array.isArray(pending)) return [];
  return pending.map((x) => (typeof x === "string" ? x : x?.span_id)).filter(Boolean);
}

/**
 * 上报 dbdog（Phase C，课题 §5 信道①）：POST 到 mcp 边缘代理（或 server 直连），
 * DD-API-KEY 鉴权（server 侧 key→org 租户路由）。两个 env 齐备才发；短超时、
 * 吞错——本地 JSONL 永远先落（真相源），上报失败不丢数据、不打扰会话。
 *   DBDOG_OBS_REPORT_URL  填 dbdog-mcp 的边缘口：http://<mcp地址>/api/v2/llmobs/spans
 *                       （mcp 原样转发内网 dbdog-server；用户机器不直连 server。
 *                        server 直连仅限内网部署场景。）
 *   DBDOG_OBS_API_KEY     dbdog API key（控制台 settings/api-keys 签发）
 */
export async function reportSpans(spans) {
  const url = process.env.DBDOG_OBS_REPORT_URL?.trim();
  const key = process.env.DBDOG_OBS_API_KEY?.trim();
  if (!url || !key || !spans.length) return false;
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json", "DD-API-KEY": key },
      body: JSON.stringify({ spans }),
      signal: AbortSignal.timeout(3000),
    });
    return response.ok;
  } catch {
    // best-effort：上报不可达不影响本地沉淀
    return false;
  }
}

/** 内容截断上限（对齐 mcp 的 DBDOG_TELEMETRY_OUTPUT_CHARS 先例，默认 8000）。 */
export function contentCap() {
  const n = Number(process.env.DBDOG_OBS_CONTENT_CHARS ?? "");
  return Number.isFinite(n) && n > 0 ? n : 8000;
}

export function cap(s) {
  if (typeof s !== "string") return null;
  const c = contentCap();
  return s.length > c ? s.slice(0, c) : s;
}

export async function readStdinJson() {
  const chunks = [];
  for await (const c of process.stdin) chunks.push(c);
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

/** 顶层包装：出错只写 stderr、永远 exit 0（hook 不得打断会话）。 */
export function run(main) {
  main().catch((err) => {
    process.stderr.write(`[dbdog-obs hook] ${err?.stack ?? err}\n`);
    process.exit(0);
  });
}
