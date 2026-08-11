#!/usr/bin/env node
// install.mjs — dbdog-agent-obs hook 一把配置（方案 B）。
//
// 目标：装 hook 时只提供 DBDOG_OBS_API_KEY 一个秘密，诊断流程总结开箱即用。
//   - 上报URL：装时一次性检出（~/.claude.json 的 mcpServers.<dbdog> → origin + /api/v2/llmobs/spans），
//     写死进 settings.json 的 env 块。地址迁了重跑本脚本或手改那行。
//   - 总结凭据：**不写**。由 summary.mjs 运行时回退到 Claude Code 自身的 ANTHROPIC_*，
//     避免把会轮换的 token 复制进 env（那是原先重复配置的病根）。
//
// 零依赖，Node ≥ 18。用法：
//   node install.mjs                                  # 交互（TTY 时）
//   node install.mjs --api-key dbdog_xxx              # 非交互给 key，URL 自动检出
//   node install.mjs --api-key dbdog_xxx --smoke -y   # 全自动 + 冒烟
//   node install.mjs --api-key dbdog_xxx --report-url http://host:port/api/v2/llmobs/spans --scope project

import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import readline from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";

const HOME = os.homedir();

// ---------- args ----------

function parseArgs(argv) {
  const o = { apiKey: null, reportUrl: null, scope: null, smoke: false, yes: false, help: false };
  for (let i = 0; i < argv.length; i++) {
    switch (argv[i]) {
      case "--api-key": o.apiKey = argv[++i]; break;
      case "--report-url": o.reportUrl = argv[++i]; break;
      case "--scope": o.scope = argv[++i]; break;
      case "--smoke": o.smoke = true; break;
      case "-y":
      case "--yes": o.yes = true; break;
      case "-h":
      case "--help": o.help = true; break;
    }
  }
  return o;
}

function printHelp() {
  console.log(`dbdog-agent-obs hook 配置器

用法：
  node install.mjs [选项]

选项：
  --api-key <dbdog_xxx>     dbdog 控制台签发的上报 key（不传则交互输入）
  --report-url <url>        上报URL（不传则从 ~/.claude.json 自动检出）
  --scope <user|project|local>  写入哪层 settings（默认 user）
  --smoke                   装完冒烟测上报通道 + 总结端点
  -y, --yes                 全程非交互，用默认/检出值
  -h, --help                本帮助

写入内容：settings.json 的 env 块新增/覆盖 DBDOG_OBS_API_KEY + DBDOG_OBS_REPORT_URL。
不写 DBDOG_SUMMARY_LLM_*——总结凭据由 summary.mjs 运行时复用 ANTHROPIC_*。`);
}

// ---------- 上报URL 检出 ----------

function readJsonSafe(file) {
  try {
    return JSON.parse(readFileSync(file, "utf8"));
  } catch {
    return null;
  }
}

// ~/.claude.json 顶层 mcpServers 里找 type:http 且名字含 dbdog 的 server。
function detectFromClaudeJson() {
  const cj = readJsonSafe(path.join(HOME, ".claude.json"));
  const buckets = [cj && cj.mcpServers];
  if (cj && cj.projects) {
    // 也看项目级 mcpServers（cwd 下）
    const proj = cj.projects[process.cwd()];
    if (proj && proj.mcpServers) buckets.push(proj.mcpServers);
  }
  for (const servers of buckets) {
    if (!servers) continue;
    for (const [name, srv] of Object.entries(servers)) {
      if (srv && srv.type === "http" && /^dbdog/i.test(name) && srv.url) {
        return { name, url: srv.url };
      }
    }
  }
  return null;
}

// 兜底：claude mcp list 解析。
function detectFromMcpList() {
  let res;
  try {
    res = spawnSync("claude", ["mcp", "list"], { encoding: "utf8", timeout: 5000 });
  } catch {
    return null;
  }
  if (!res || res.status !== 0 || !res.stdout) return null;
  for (const line of res.stdout.split("\n")) {
    const m = line.match(/^(\S*dbdog\S*):\s+(\S+)\s+\(HTTP\)/i);
    if (m) return { name: m[1], url: m[2] };
  }
  return null;
}

function toReportUrl(mcpUrl) {
  try {
    return `${new URL(mcpUrl).origin}/api/v2/llmobs/spans`;
  } catch {
    return null;
  }
}

function detectReportUrl() {
  const det = detectFromClaudeJson() || detectFromMcpList();
  if (!det) return { url: null, source: null };
  return { url: toReportUrl(det.url), source: `${det.url}` };
}

// ---------- settings.json 合并 ----------

function scopeFile(scope) {
  if (scope === "project") return path.join(process.cwd(), ".claude", "settings.json");
  if (scope === "local") return path.join(process.cwd(), ".claude", "settings.local.json");
  return path.join(HOME, ".claude", "settings.json");
}

// 备份后把 kv 合并进 .env（幂等：覆盖同名 key、保留其它）。返回 { file, hadSummaryKeys }。
function mergeEnv(file, kv) {
  if (existsSync(file)) copyFileSync(file, `${file}.bak`);
  let cfg;
  try {
    cfg = existsSync(file) ? JSON.parse(readFileSync(file, "utf8") || "{}") : {};
  } catch (e) {
    throw new Error(`${file} 解析失败：${e.message}（已备份为 ${file}.bak，修复后重跑）`);
  }
  if (typeof cfg !== "object" || cfg === null || Array.isArray(cfg)) {
    throw new Error(`${file} 顶层不是 JSON 对象，不敢改`);
  }
  const hadSummaryKeys = cfg.env && Object.keys(cfg.env).some((k) => k.startsWith("DBDOG_SUMMARY_LLM_"));
  cfg.env = cfg.env || {};
  for (const [k, v] of Object.entries(kv)) cfg.env[k] = v;
  mkdirSync(path.dirname(file), { recursive: true });
  writeFileSync(file, `${JSON.stringify(cfg, null, 2)}\n`);
  return { file, hadSummaryKeys: !!hadSummaryKeys };
}

// ---------- 冒烟 ----------

async function smokeReport(url, apiKey) {
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json", "DD-API-KEY": apiKey },
      body: JSON.stringify({ spans: [] }),
      signal: AbortSignal.timeout(5000),
    });
    // 400 "spans 为空" = 连得上 + key 被认；2xx 也通；401/403 = key 错；5xx/网络错 = 不通
    const ok = res.status < 500;
    const note =
      res.status === 400 ? "HTTP 400 spans 为空 → 连接 OK"
      : res.status === 401 || res.status === 403 ? `HTTP ${res.status} → key 可能不对`
      : `HTTP ${res.status}`;
    return { ok, note };
  } catch (e) {
    return { ok: false, note: e.message };
  }
}

async function smokeSummary() {
  // 复用 summary.mjs 的 summaryEnv（含 ANTHROPIC_* 回退），保证和线上同一条解析路径。
  const mod = await import(new URL("./summary.mjs", import.meta.url).href);
  const env = mod.summaryEnv();
  if (!env) return { ok: false, note: "未解析出总结凭据（ANTHROPIC_* 与 DBDOG_SUMMARY_LLM_* 均缺）" };
  try {
    const res = await fetch(`${env.baseUrl.replace(/\/+$/, "")}/v1/messages`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${env.apiKey}`,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify({ model: env.model, max_tokens: 1, messages: [{ role: "user", content: "ping" }] }),
      signal: AbortSignal.timeout(10_000),
    });
    return { ok: res.ok, note: `HTTP ${res.status} · ${env.baseUrl} · ${env.model}` };
  } catch (e) {
    return { ok: false, note: e.message };
  }
}

// ---------- 交互辅助 ----------

async function ask(rl, q, dflt) {
  const tail = dflt ? ` [${dflt}] ` : " ";
  const a = (await rl.question(q + tail)).trim();
  return a || (dflt != null ? String(dflt) : "");
}

async function askYesNo(rl, q, dflt = true) {
  const a = (await rl.question(`${q} (${dflt ? "Y/n" : "y/N"}) `)).trim().toLowerCase();
  if (!a) return dflt;
  return /^[yY]/.test(a);
}

function fail(msg) {
  console.error(`✗ ${msg}`);
  process.exit(1);
}

// ---------- main ----------

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    printHelp();
    return;
  }
  const interactive = !!process.stdin.isTTY && !args.yes;
  const rl = interactive ? readline.createInterface({ input, output }) : null;

  // 1) 上报URL
  let reportUrl = args.reportUrl;
  let urlSource = "--report-url";
  if (!reportUrl) {
    const det = detectReportUrl();
    if (det.url) {
      reportUrl = det.url;
      urlSource = `自动检出（dbdog MCP: ${det.source}）`;
      if (rl) {
        const ok = await askYesNo(rl, `上报URL 检出为\n  ${reportUrl}\n  采用？`, true);
        if (!ok) reportUrl = await ask(rl, "输入上报URL（…/api/v2/llmobs/spans）：");
      }
    } else if (rl) {
      reportUrl = await ask(rl, "未自动检出 dbdog MCP 地址。\n输入上报URL（…/api/v2/llmobs/spans）：");
    } else {
      fail("未自动检出上报URL，且未传 --report-url（非交互模式下请用 --report-url 或在 TTY 运行）");
    }
  }
  if (!reportUrl || !/\/api\/v2\/llmobs\/spans/.test(reportUrl)) {
    console.error(`⚠ 上报URL 看起来不像 …/api/v2/llmobs/spans：${reportUrl}`);
  }

  // 2) dbdog API key
  let apiKey = args.apiKey;
  if (!apiKey && rl) apiKey = (await ask(rl, "粘贴 dbdog_ 前缀的 API key：")).trim();
  if (!apiKey) fail("缺少 dbdog API key（--api-key 或交互输入）");
  if (!/^dbdog_/.test(apiKey)) {
    console.error(`⚠ key 不是 dbdog_ 前缀（${apiKey.slice(0, 6)}…）。dbdog 控制台 settings/api-keys 签发的是 dbdog_ 开头。`);
  }

  // 3) 总结凭据探测（只报告，不写）
  const anthTok = process.env.ANTHROPIC_AUTH_TOKEN || process.env.ANTHROPIC_API_KEY;
  const anthUrl = process.env.ANTHROPIC_BASE_URL || process.env.ANTHROPIC_API_URL;
  if (anthTok) {
    console.log(`✓ 检出 ${process.env.ANTHROPIC_AUTH_TOKEN ? "ANTHROPIC_AUTH_TOKEN" : "ANTHROPIC_API_KEY"}${anthUrl ? " + ANTHROPIC_BASE_URL" : ""}：诊断总结将运行时复用，无需另配 DBDOG_SUMMARY_LLM_*。`);
  } else {
    console.log("⚠ 未检出 ANTHROPIC_*：诊断总结将不可用。需先让 Claude Code 带 ANTHROPIC_* 运行，或显式配 DBDOG_SUMMARY_LLM_*。");
  }

  // 4) scope
  let scope = args.scope || "user";
  if (rl) {
    scope = (await ask(rl, "写入哪层 settings？(user=~/​.claude 全局 / project=本项目 / local=本项目本地)", scope)).trim() || "user";
  }
  if (!["user", "project", "local"].includes(scope)) fail(`未知 scope：${scope}`);
  const file = scopeFile(scope);

  // 5) 写 env 块
  const { hadSummaryKeys } = mergeEnv(file, {
    DBDOG_OBS_API_KEY: apiKey,
    DBDOG_OBS_REPORT_URL: reportUrl,
  });
  console.log(`✓ 已写入 ${file} 的 env 块（备份 ${file}.bak）：`);
  console.log("    DBDOG_OBS_API_KEY + DBDOG_OBS_REPORT_URL");
  console.log(`  （来源：${urlSource}）`);

  // 6) 冗余 DBDOG_SUMMARY_LLM_* 提示（已能走 ANTHROPIC_* 回退）
  if (hadSummaryKeys && anthTok) {
    console.log("ℹ 检测到 env 里仍残留 DBDOG_SUMMARY_LLM_*。现已走 ANTHROPIC_* 回退，可删除它们以免 token 轮换两处不同步。");
    if (rl) {
      const rm = await askYesNo(rl, "  现在帮你删掉这 3 个残留 key？", true);
      if (rm) {
        const cfg = JSON.parse(readFileSync(file, "utf8"));
        for (const k of Object.keys(cfg.env || {})) {
          if (k.startsWith("DBDOG_SUMMARY_LLM_")) delete cfg.env[k];
        }
        writeFileSync(file, `${JSON.stringify(cfg, null, 2)}\n`);
        console.log("  ✓ 已删除 DBDOG_SUMMARY_LLM_* 残留。");
      }
    }
  }

  // 7) 冒烟（可选）
  if (args.smoke) {
    console.log("\n— 冒烟测试 —");
    const r1 = await smokeReport(reportUrl, apiKey);
    console.log(`${r1.ok ? "✓" : "✗"} 上报通道：${r1.note}`);
    const r2 = await smokeSummary();
    console.log(`${r2.ok ? "✓" : "✗"} 总结端点：${r2.note}`);
  }

  console.log("\n下一步：输入 /hooks 回车（或开新会话）让配置生效；然后发一条「诊断: …」验证。");
  if (rl) rl.close();
}

main().catch((e) => {
  console.error(`✗ ${e.stack || e.message}`);
  process.exit(1);
});
