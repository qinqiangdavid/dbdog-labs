#!/usr/bin/env node
// SessionEnd — 会话直接结束的收尾（2026-08-14，47 圈 headless 巡检实测定案）。
//
// 成因：carry 机制（v0.4.4）只在「下一次 UserPromptSubmit」铸造/停用时记账；
// 一次性会话（claude -p）或直接关窗没有下一次——Stop 读 transcript 时本轮收尾那几行
// （正是产出结论的 assistant 行）还没落盘，[cursor, EOF] 永久没人合成。
// 实测 47 圈巡检 46 圈丢尾部 llm span（每圈 1–6 条）；更狠的是会话退出时仍在跑的
// 子代理连 SubagentStop 都不会触发，整棵子树消失（单圈实测丢 241 条）。
//
// 收尾三件事（全部幂等：span_id/ts 皆确定性派生，重入只推游标不重发）：
//   ① 先收 carry（换过话题遗留的旧 trace 尾巴，与 stop.mjs 同一段逻辑）；
//   ② 主线补 [cursor, EOF]——SessionEnd 时 transcript 已写完，读到哪算哪；
//   ③ 子代理 transcript 挨个补到 EOF：有状态文件的续 cursor；一次都没被
//      SubagentStop 处理过的（in-flight 退出）整棵合成，agent_type 从
//      agent-<id>.meta.json 读（SubagentStop 的 input.agent_type 此刻已无从拿）。
//
// 触发门与 Stop 对齐：state.active === false 时只收 carry（②③ 不做——停用后的
// 行不属于任何 trace，子代理丢弃是 user-prompt-submit.mjs 已记档的既有语义）。
//
// 上报分批（每批 ≤100）：收尾批可能有几百条（in-flight 子代理整棵），单发大包会
// 骑在 3s 超时上；失败的照旧记 pending_spans，留给下一次 sweep。
//
// ④ 收尾出了新 span 就再触发一次诊断总结（detached，同 stop.mjs）——此时尾部结论
//    span 已补齐，总结吃到的是完整 trace。Stop 时刻的 spawn 保持不动：两个 worker
//    产同键同 ts 的 workflow span，读侧后写赢，互不打架。
import fs from "node:fs";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  appendSpans,
  cap,
  deriveSpanId,
  lookupSpans,
  pendingIds,
  readState,
  readStdinJson,
  reportSpans,
  run,
  writeState,
} from "./lib.mjs";
import { PENDING_TOOL_USE_MAX, msBetween, readNewLines, synthesize } from "./synthesize.mjs";
import { summaryEnv } from "./summary.mjs";

/** 诊断流程总结 detached worker（与 stop.mjs 用同一个）。 */
const WORKER = path.join(path.dirname(fileURLToPath(import.meta.url)), "summary-worker.mjs");

/** 单批上报条数上限（对齐 sweep 的量级；服务端限 1000 条/5MB，留足余量）。 */
const BATCH = 100;

/** 落盘 + 分批上报；返回未送达的 span_id 列表（含 carriedOverIds 里重发失败的）。 */
async function emitBatched(spans, carriedOverIds) {
  appendSpans(spans);
  const batch = [...lookupSpans(carriedOverIds), ...spans];
  const failed = [];
  for (let i = 0; i < batch.length; i += BATCH) {
    const part = batch.slice(i, i + BATCH);
    if (!(await reportSpans(part))) failed.push(...part.map((s) => s.span_id));
  }
  return failed;
}

/** carry 收尾：与 stop.mjs flushCarry 同构（SessionEnd 也可能是交界后的第一个事件）。 */
async function flushCarry(input, state) {
  const c = state.carry;
  if (!c) return false;
  delete state.carry;
  const transcript = c.transcript_path ?? input.transcript_path ?? state.transcript_path;
  if (!c.trace_id || !transcript || !(c.to > c.from)) return true;
  let lines;
  try {
    ({ lines } = readNewLines(transcript, c.from, c.to));
  } catch {
    return true; // transcript 已被清理——没得补，别打断收尾
  }
  if (!lines.length) return true;
  const { spans } = synthesize({
    lines,
    traceId: c.trace_id,
    sessionId: c.session_id ?? state.session_id,
    parentId: c.root_span_id,
    mlApp: c.ml_app,
    pendingToolUses: new Map(Object.entries(c.pending_tool_uses ?? {})),
    lastEntryTs: c.last_entry_ts ?? null,
    agent: null,
    ctxBuf: "",
  });
  if (!spans.length) return true;
  const pending = await emitBatched(spans, []);
  state.pending_spans = [...pendingIds(state.pending_spans), ...pending];
  return true;
}

/** 主线尾巴：[cursor, EOF]，与 handleMain 同粒度，但不再重发 root span——
 *  root 在每次 Stop 已"后写赢"落定，SessionEnd 没有 last_assistant_message 可更新。 */
async function flushMainTail(input, state) {
  const transcript = input.transcript_path ?? state.transcript_path;
  if (!transcript) return 0;
  let lines, nextCursor;
  try {
    ({ lines, nextCursor } = readNewLines(transcript, state.cursor ?? 0));
  } catch {
    return 0; // transcript 不在了，无尾可收
  }
  if (!lines.length) return 0;
  const { spans, pendingToolUses, lastEntryTs, ctxBuf } = synthesize({
    lines,
    traceId: state.trace_id,
    sessionId: state.session_id,
    parentId: state.root_span_id,
    mlApp: state.ml_app,
    pendingToolUses: new Map(Object.entries(state.pending_tool_uses ?? {})),
    lastEntryTs: state.last_entry_ts ?? null,
    agent: null,
    ctxBuf: state.ctx_buf ?? "",
  });
  const pending = await emitBatched(spans, pendingIds(state.pending_spans));
  state.cursor = nextCursor;
  state.pending_spans = pending;
  state.last_entry_ts = lastEntryTs;
  state.ctx_buf = ctxBuf;
  state.pending_tool_uses = Object.fromEntries(
    [...pendingToolUses.entries()].slice(-PENDING_TOOL_USE_MAX),
  );
  return spans.length;
}

/**
 * 子代理收尾：<transcript 同目录>/<session_id>/subagents/agent-*.jsonl 挨个补到 EOF。
 * 与 handleSubagent 的差别只有两处：agent_type 从 meta.json 读；
 * output 没有 input.last_assistant_message 可用，取子代理 transcript 里最后一段
 * 助手文本作近似（agent span 同 span_id 重发、读侧后写赢，SubagentStop 发过的不受损）。
 */
async function flushSubagents(input, state) {
  const transcript = input.transcript_path ?? state.transcript_path;
  const sessionId = input.session_id;
  if (!transcript || !sessionId) return 0;
  const subDir = path.join(path.dirname(transcript), sessionId, "subagents");
  let files;
  try {
    files = fs.readdirSync(subDir).filter((f) => f.startsWith("agent-") && f.endsWith(".jsonl"));
  } catch {
    return 0; // 没起过子代理
  }
  let flushed = 0;
  for (const f of files) {
    const agentId = f.slice("agent-".length, -".jsonl".length);
    const at = path.join(subDir, f);
    const sub = readState(sessionId, agentId) ?? {};
    let lines, nextCursor;
    try {
      ({ lines, nextCursor } = readNewLines(at, sub.cursor ?? 0));
    } catch {
      continue;
    }
    if (!lines.length) continue;

    let agentType = null;
    try {
      agentType = JSON.parse(fs.readFileSync(at.slice(0, -".jsonl".length) + ".meta.json", "utf8"))
        .agentType ?? null;
    } catch {
      /* 没 meta 就不打 agent_type */
    }

    const selfSpanId = deriveSpanId(state.trace_id, agentId);
    const parentToolSpanId = deriveSpanId(state.trace_id, `tool:${agentId}`);
    const { spans, pendingToolUses, lastEntryTs, firstEntryTs, firstUserText, ctxBuf } = synthesize({
      lines,
      traceId: state.trace_id,
      sessionId: state.session_id ?? sessionId,
      parentId: selfSpanId,
      mlApp: state.ml_app,
      pendingToolUses: new Map(Object.entries(sub.pending_tool_uses ?? {})),
      lastEntryTs: sub.last_entry_ts ?? null,
      agent: { id: agentId, type: agentType },
      ctxBuf: sub.ctx_buf ?? "",
    });

    const startedAt = sub.started_at ?? firstEntryTs;
    const prompt = sub.prompt ?? firstUserText;
    if (startedAt) {
      // 最后一段助手文本 = last_assistant_message 的近似（倒序找第一个非空文本块）
      let lastText = "";
      for (let i = lines.length - 1; i >= 0 && !lastText; i--) {
        try {
          const e = JSON.parse(lines[i]);
          if (e?.type === "assistant" && Array.isArray(e.message?.content)) {
            lastText = e.message.content
              .map((b) => (b?.type === "text" ? b.text : ""))
              .filter(Boolean)
              .join("\n");
          }
        } catch {
          /* 脏行跳过 */
        }
      }
      spans.push({
        trace_id: state.trace_id,
        span_id: selfSpanId,
        parent_id: parentToolSpanId,
        session_id: state.session_id ?? sessionId,
        kind: "agent",
        name: "claude-code.subagent",
        model: null,
        status: "ok",
        ts: startedAt,
        duration_ms: msBetween(startedAt, lastEntryTs),
        input: cap(prompt ?? ""),
        output: cap(lastText),
        tokens_input: null,
        tokens_output: null,
        tokens_cache_read: null,
        tokens_cache_creation: null,
        tags: {
          sidechain: "1",
          agent_id: agentId,
          ...(agentType ? { agent_type: agentType } : {}),
          ...(state.ml_app ? { ml_app: state.ml_app } : {}),
        },
      });
    }

    const pending = await emitBatched(spans, pendingIds(sub.pending_spans));
    flushed += spans.length;
    writeState(
      sessionId,
      {
        cursor: nextCursor,
        pending_spans: pending,
        last_entry_ts: lastEntryTs,
        started_at: startedAt ?? null,
        prompt: prompt ?? null,
        pending_tool_uses: Object.fromEntries([...pendingToolUses.entries()].slice(-PENDING_TOOL_USE_MAX)),
        ctx_buf: ctxBuf,
      },
      agentId,
    );
  }
  return flushed;
}

run(async () => {
  const input = await readStdinJson();
  const state = readState(input.session_id);
  if (!state?.trace_id) return; // 无 trace 归属 → 无尾可收

  const flushed = await flushCarry(input, state);
  if (state.active === false) {
    // 停用后的行不属于任何 trace；子代理丢弃是既有语义（见 user-prompt-submit.mjs）
    if (flushed) writeState(input.session_id, state);
    return;
  }
  const flushedMain = await flushMainTail(input, state);
  const flushedSub = await flushSubagents(input, state);
  writeState(input.session_id, state);

  // 收尾出了新 span → 总结重算一次（吃到补齐后的完整 trace）。detached：SessionEnd 的
  // 30s 超时罩不住 LLM 调用，且 worker 失败自己会在 summary-worker.log 留痕。
  if (flushedMain + flushedSub > 0 && summaryEnv()) {
    try {
      spawn(process.execPath, [WORKER, input.session_id], {
        detached: true,
        stdio: "ignore",
      }).unref();
    } catch {
      /* best-effort：起不来就这次没总结，不影响收尾 */
    }
  }
});
