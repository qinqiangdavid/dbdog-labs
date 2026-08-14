#!/usr/bin/env node
// summary-worker.mjs — 诊断流程总结 detached worker。
// 由 stop.mjs handleMain 在「本 trace span 增长」时 spawn({detached:true}).unref() 起本进程，
// 后台跑（用户零等待）：读本 trace 的 span → 裁剪（Y 方案）→ 调本地大模型 → 组装 workflow
// 总结 span → appendSpans（本地真相源）+ reportSpans（推 server）。
// 重复生成落同一行：span_id 派生固定 + ts 锚 state.started_at（两者都进 ClickHouse 排序键，
// 只固定 span_id 折不掉）→ 后写赢。
// best-effort：env 未配 / 任何失败 → 直接返回，不打扰任何人（run() 兜底吞错、exit 0）。
// 失败留痕（2026-08-14）：本进程 detached + stdio ignore，run() 只写 stderr 等于写进黑洞
// ——45/47 圈巡检没总结、死因（推理模型 thinking 烧光 max_tokens）藏了两天没人知道。
// 现在任何失败在 obsDir/summary-worker.log 追加一行（时间戳 + session + 错误），可诊断、
// 不打扰会话；日志只追加小行，不设轮转（量级 = 每次失败一行）。
// 用法：node summary-worker.mjs <sessionId>
import fs from "node:fs";
import path from "node:path";
import { obsDir, readState, spanIndex, appendSpans, reportSpans, deriveSpanId, pendingIds, run, writeState } from "./lib.mjs";
import { trimSpans, buildPrompt, generateSummary, summaryEnv } from "./summary.mjs";

const SUMMARY_KIND = "workflow";
const SUMMARY_NAME = "diagnosis-summary";

/** 失败落一行；日志本身失败就算了（绝不因留痕再抛）。 */
function logFailure(sessionId, err) {
  try {
    fs.appendFileSync(
      path.join(obsDir(), "summary-worker.log"),
      `${new Date().toISOString()} session=${sessionId} ${err?.message ?? err}\n`,
    );
  } catch {
    /* 留不下就留不下 */
  }
}

run(async () => {
  const sessionId = process.argv[2];
  if (!sessionId) return;
  try {
    await main(sessionId);
  } catch (err) {
    logFailure(sessionId, err);
  }
});

async function main(sessionId) {
  const state = readState(sessionId);
  if (!state?.trace_id || !state?.root_span_id) return;

  const env = summaryEnv();
  if (!env) return; // 未配 → 不出总结（不影响 trace）

  // 本 trace 的 span（真相源 = spans.jsonl）
  const spans = [...spanIndex().values()].filter((s) => s.trace_id === state.trace_id);
  if (spans.length === 0) return;

  // 快照水位（codex 复审:代次校验）：Stop 与 SessionEnd 各 spawn 一个 worker 时，
  // 旧快照（span 少）的那个可能因 LLM 慢而后写，把完整总结覆盖回残缺版。固定键只保
  // 「可折叠」，不保「新的赢」——写之前按水位（本快照的 span 数）比较，矮水位丢弃自己。
  // 标记文件取 .summary-gen.json 后缀：sweep 会把它当已排空的子代理状态走 1 天 TTL 清理。
  const watermark = spans.length;
  const genPath = path.join(obsDir(), `${state.trace_id}.summary-gen.json`);

  // 裁剪 → 提示词 → 本地大模型（失败抛 → run() 吞）
  const factTable = trimSpans(spans);
  const result = await generateSummary(buildPrompt(factTable), env);

  let existing = null;
  try {
    existing = JSON.parse(fs.readFileSync(genPath, "utf8"));
  } catch {
    /* 没有标记 = 首个写者 */
  }
  if (existing && Number(existing.watermark) > watermark) {
    logFailure(sessionId, `stale snapshot dropped: watermark ${watermark} < ${existing.watermark}`);
    return;
  }
  try {
    fs.writeFileSync(genPath, JSON.stringify({ watermark }));
  } catch {
    /* 标记写不动就退回后写赢,不阻塞总结 */
  }

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
    // ts 锚在 trace 起点，不能取 new Date()：总结会在每个"有新工具调用"的 Stop 之后重算，
    // 而 ClickHouse 那张表排序键是 (trace_id, ts, span_id)——ts 一变就是新行、FINAL 折不掉，
    // 平台上会堆好几条总结，控制台 findSummarySpan 按 ts 序 .find() 到的还是最早那条（过期）。
    // 固定 span_id 只解决一半，ts 也必须稳定。缺 started_at 的老状态退回墙上时钟。
    ts: state.started_at ?? new Date().toISOString(),
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
  if (!(await reportSpans([summarySpan]))) {
    // 送达失败（codex 复审）：一次性会话之后没有下一轮重试,不落 pending 平台就永久没有。
    // span_id 记进主状态 pending_spans,交给之后任何一次 sweep 补发;同时留痕。
    try {
      const cur = readState(sessionId) ?? state;
      cur.pending_spans = [...pendingIds(cur.pending_spans), summarySpan.span_id];
      writeState(sessionId, cur);
    } catch {
      /* 状态写不动,至少还有日志与本地 JSONL */
    }
    logFailure(sessionId, "summary span report failed（已落本地与 pending，待 sweep 补发）");
  }
}
