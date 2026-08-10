#!/usr/bin/env node
// summary-worker.mjs — 诊断流程总结 detached worker。
// 由 stop.mjs handleMain 在「本 trace span 增长」时 spawn({detached:true}).unref() 起本进程，
// 后台跑（用户零等待）：读本 trace 的 span → 裁剪（Y 方案）→ 调本地大模型 → 组装 workflow
// 总结 span → appendSpans（本地真相源）+ reportSpans（推 server）。固定 span_id → 后写赢。
// best-effort：env 未配 / 任何失败 → 直接返回，不打扰任何人（run() 兜底吞错、exit 0）。
// 用法：node summary-worker.mjs <sessionId>
import { readState, spanIndex, appendSpans, reportSpans, deriveSpanId, run } from "./lib.mjs";
import { trimSpans, buildPrompt, generateSummary, summaryEnv } from "./summary.mjs";

const SUMMARY_KIND = "workflow";
const SUMMARY_NAME = "diagnosis-summary";

run(async () => {
  const sessionId = process.argv[2];
  if (!sessionId) return;
  const state = readState(sessionId);
  if (!state?.trace_id || !state?.root_span_id) return;

  const env = summaryEnv();
  if (!env) return; // 未配 → 不出总结（不影响 trace）

  // 本 trace 的 span（真相源 = spans.jsonl）
  const spans = [...spanIndex().values()].filter((s) => s.trace_id === state.trace_id);
  if (spans.length === 0) return;

  // 裁剪 → 提示词 → 本地大模型（失败抛 → run() 吞）
  const factTable = trimSpans(spans);
  const result = await generateSummary(buildPrompt(factTable), env);

  // 组装总结 span（固定 span_id → 后写赢，重复生成覆盖同一行）
  const summarySpan = {
    trace_id: state.trace_id,
    span_id: deriveSpanId(state.trace_id, "diag-summary"),
    parent_id: state.root_span_id,
    session_id: state.session_id ?? sessionId,
    kind: SUMMARY_KIND,
    name: SUMMARY_NAME,
    model: null,
    status: "ok",
    ts: new Date().toISOString(),
    duration_ms: null,
    input: null,
    output: result.text,
    intent: undefined,
    tokens_input: null,
    tokens_output: null,
    tokens_cache_read: null,
    tokens_cache_creation: null,
    tags: {
      trace_source: "client",
      summary_model: result.model,
      ...(result.tokens_input != null ? { summary_tokens_in: String(result.tokens_input) } : {}),
      ...(result.tokens_output != null ? { summary_tokens_out: String(result.tokens_output) } : {}),
      ...(state.ml_app ? { ml_app: state.ml_app } : {}),
    },
  };

  appendSpans([summarySpan]); // 本地真相源先落
  await reportSpans([summarySpan]); // best-effort：推失败也有本地记录
});
