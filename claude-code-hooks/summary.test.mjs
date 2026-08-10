import { afterEach, describe, expect, it } from "vitest";
import { trimSpans, buildPrompt, generateSummary, summaryEnv, SYSTEM_PROMPT } from "./summary.mjs";

const baseEnv = {
  DBDOG_SUMMARY_LLM_BASE_URL: "https://open.bigmodel.cn/api/anthropic",
  DBDOG_SUMMARY_LLM_API_KEY: "sk-test",
};

afterEach(() => {
  delete process.env.DBDOG_SUMMARY_LLM_BASE_URL;
  delete process.env.DBDOG_SUMMARY_LLM_API_KEY;
  delete process.env.DBDOG_SUMMARY_LLM_MODEL;
  delete process.env.DBDOG_SUMMARY_LLM_TIMEOUT_MS;
});

describe("summaryEnv", () => {
  it("未配 / 占位 → null（不出总结）", () => {
    expect(summaryEnv()).toBeNull();
    process.env.DBDOG_SUMMARY_LLM_BASE_URL = baseEnv.DBDOG_SUMMARY_LLM_BASE_URL;
    process.env.DBDOG_SUMMARY_LLM_API_KEY = "change-me";
    expect(summaryEnv()).toBeNull();
    process.env.DBDOG_SUMMARY_LLM_API_KEY = "<填GLM key>";
    expect(summaryEnv()).toBeNull();
  });

  it("配齐 → 返回 config，model 默认 glm-5.2", () => {
    Object.assign(process.env, baseEnv);
    const env = summaryEnv();
    expect(env).not.toBeNull();
    expect(env.model).toBe("glm-5.2");
    expect(env.timeoutMs).toBe(30_000);
    process.env.DBDOG_SUMMARY_LLM_MODEL = "glm-4";
    expect(summaryEnv().model).toBe("glm-4");
  });
});

const sampleSpans = [
  { span_id: "root", kind: "agent", parent_id: null, ts: 0, output: "根因：rownum 绑定到 aggstate，ps_rownum 恒为 0，max(rownum)=1。" },
  { span_id: "t1", kind: "tool", name: "search_dbdog_database_samples", ts: 1000, intent: "找目标 SQL", tags: { mcp_server: "dbdog" }, output: "命中 0 条" },
  { span_id: "t2", kind: "tool", name: "search_dbdog_logs", ts: 2000, intent: "找目标 SQL", tags: { mcp_server: "dbdog" }, output: "0 matches" },
  { span_id: "t3", kind: "tool", name: "Read", ts: 3000, input: "nodeAgg.c", output: "rnstate->ps=parent\ncombined_inputeval aggstate" },
  { span_id: "t4", kind: "tool", name: "TaskCreate", ts: 4000, output: "todo" },
  { span_id: "l1", kind: "llm", ts: 5000, output: "我去查 [tool_use: search_dbdog_logs]" },
];

describe("trimSpans（Y 方案）", () => {
  const fact = trimSpans(sampleSpans);

  it("MCP 工具按 intent 归组、同 intent 合并、留信号行", () => {
    expect(fact).toContain("找目标 SQL");
    expect(fact).toContain("命中 0");
    expect(fact).toContain("0 matches");
  });

  it("代码证据（Read/Grep/Bash）保留 file/函数/行", () => {
    expect(fact).toContain("nodeAgg");
    expect(fact).toContain("aggstate");
  });

  it("agent 结论整段留", () => {
    expect(fact).toContain("根因：rownum 绑定到 aggstate");
  });

  it("丢 llm 与管理工具（TaskCreate、tool_use 标记）", () => {
    expect(fact).not.toContain("TaskCreate");
    expect(fact).not.toContain("[tool_use");
    expect(fact).not.toContain("todo");
  });
});

describe("buildPrompt", () => {
  it("system + user 双角色，system 含写作规则", () => {
    const prompt = buildPrompt("事实表");
    expect(prompt.map((m) => m.role)).toEqual(["system", "user"]);
    expect(prompt[0].content).toBe(SYSTEM_PROMPT);
    expect(SYSTEM_PROMPT).toContain("具体数值");
    expect(SYSTEM_PROMPT).toContain("起·承·转·合");
    expect(prompt[1].content).toBe("事实表");
  });
});

describe("generateSummary", () => {
  it("POST /v1/messages，从 content[].text 取正文，附 token", async () => {
    Object.assign(process.env, baseEnv);
    const env = summaryEnv();
    let captured;
    const origFetch = globalThis.fetch;
    globalThis.fetch = async (url, init) => {
      captured = { url: String(url), body: JSON.parse(init.body) };
      return new Response(
        JSON.stringify({ content: [{ type: "text", text: "一段诊断叙事。" }], usage: { input_tokens: 100, output_tokens: 20 } }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    };
    try {
      const out = await generateSummary(buildPrompt("事实表"), env);
      expect(out.text).toBe("一段诊断叙事。");
      expect(out.tokens_input).toBe(100);
      expect(out.tokens_output).toBe(20);
      expect(captured.url.endsWith("/v1/messages")).toBe(true);
      expect(captured.body.model).toBe("glm-5.2");
      expect(captured.body.messages.some((m) => m.role === "user")).toBe(true);
    } finally {
      globalThis.fetch = origFetch;
    }
  });

  it("HTTP 非 2xx / 空 content → 抛（上层 best-effort 吞）", async () => {
    Object.assign(process.env, baseEnv);
    const env = summaryEnv();
    const origFetch = globalThis.fetch;
    globalThis.fetch = async () => new Response("nope", { status: 429 });
    try {
      await expect(generateSummary(buildPrompt("x"), env)).rejects.toThrow(/HTTP 429/);
    } finally {
      globalThis.fetch = origFetch;
    }
  });
});
