#!/usr/bin/env node
// SessionStart — 只干一件事：把 sweep.mjs 甩到后台去收尸，然后立刻退出。
//
// 为什么要 detached：收尸可能要补发几百条 span、发好几轮 HTTP（每轮 3s 超时）。
// 会话启动时用户在等，绝不能把这段耗时压在启动路径上。detached + unref 之后
// 本进程立即退出，sweep 在后台自己跑完。
//
// 为什么挂 SessionStart 而不是 Stop：stop.mjs 开头就因触发门提前返回
// （`!state?.trace_id || state.active === false`），triggered 模式下绝大多数会话
// 根本不触发——而积压恰恰发生在"观测开过、然后会话结束"之后，越需要收尸越轮不到它。
// SessionStart 与触发门无关，每次开会话都跑。
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { readStdinJson, run } from "./lib.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));

run(async () => {
  // 先甩出去再读 stdin——万一 stdin 是坏 JSON，也不能耽误收尸
  try {
    spawn(process.execPath, [path.join(HERE, "sweep.mjs")], {
      detached: true,
      stdio: "ignore",
      env: process.env,
    }).unref();
  } catch {
    /* 起不来就算了，下次会话再试；绝不打断会话 */
  }

  // 把 stdin 读掉，免得写侧拿到 EPIPE
  try {
    await readStdinJson();
  } catch {
    /* hook 不关心内容 */
  }
});
