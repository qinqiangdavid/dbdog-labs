#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""evidence-chain:事故窗 + 现象 + 根因 (+ 修复 diff) (+ 源码树) → 反向取证(evidence-chain.md / .json)。
推导角带 dbdog MCP 工具,按根因推出该有的证据并真去取,边取边推;记每条证据的取证结果与 dbdog 侧缺口。
跨平台(Windows / macOS / Linux),只用 Python 标准库;推导本身由 `claude -p` 完成,模型会读源码树。

  python run.py --phenomenon 题面.txt --root-cause 根因.md --window "2026-09-09 09:04–09:07" [--fix fix.diff|PR链接] [--source 源码树] --out 目录
  python run.py <题目目录> [--source 源码树]     # 目录里有 prompt.txt / ground-truth.md (/ fix.diff)
  可选:--mcp-config mcp.json(显式挂 dbdog MCP;不给则继承 CLAUDE_CONFIG_DIR 里已配的)  --config-dir <CLAUDE_CONFIG_DIR>  --model <名>  --force
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
CATALOG = os.path.join(SKILL, "references", "dbdog-tool-catalog.json")
PROMPT = os.path.join(SKILL, "prompts", "chain.md")
DISALLOWED = "Bash,WebFetch,WebSearch,Agent,Task,NotebookEdit"  # 只禁本地执行/联网/派子代理;Write/Edit 要留着写产物(工作目录是临时目录),Read/Grep/Glob 与 dbdog MCP 放行
OUT_MD, OUT_JSON = "evidence-chain.md", "evidence-chain.json"


def log(msg):
    print(f"[evidence-chain] {msg}", file=sys.stderr)


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def case_md(phenomenon_file, root_cause_file, window=None):
    out = ["# 反向输入", ""]
    if window:
        out += ["## 事故窗(用例执行时间,取证一律用这个窗)", "", window.strip(), ""]
    out += ["## 现象(喂给被测 agent 的题面原话,即「诊断:」后面跟的那段)", "", read(phenomenon_file).strip(), ""]
    m = re.search(r"^##\s*现象量化[^\n]*\n(.*?)(?=^##|\Z)", read(root_cause_file), re.S | re.M)
    if m:
        out += ["## 现象量化(实测)", "", m.group(1).strip(), ""]
    return "\n".join(out)


def diff_url(fix):
    """--fix 是链接时映射到能直接下载的 diff 地址;本地路径返回 None。"""
    if not re.match(r"^https?://", fix or ""):
        return None
    if re.search(r"\.(diff|patch)$", fix):
        return fix
    if re.search(r"(github\.com/[^/]+/[^/]+/(pull|commit)/[^/?#]+|gitee\.com/[^/]+/[^/]+/(pulls|commit)/[^/?#]+)$", fix):
        return fix.rstrip("/") + ".diff"
    return fix


def load_fix(fix):
    if not fix:
        return None
    url = diff_url(fix)
    if url:
        log(f"下载修复 diff:{url}")
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "evidence-chain"}), timeout=60) as r:
            return r.read().decode("utf-8", "replace")
    if not os.path.isfile(fix):
        raise SystemExit(f"找不到修复 diff:{fix}")
    return read(fix)


def prepare_workdir(phenomenon, root_cause, fix_text, source, work=None, window=None):
    work = work or tempfile.mkdtemp(prefix="evidence-chain-")
    os.makedirs(work, exist_ok=True)
    write(os.path.join(work, "case.md"), case_md(phenomenon, root_cause, window))
    shutil.copyfile(root_cause, os.path.join(work, "root-cause.md"))
    if fix_text:
        write(os.path.join(work, "fix.diff"), fix_text)
    shutil.copyfile(CATALOG, os.path.join(work, "tool-catalog.json"))
    if source and os.path.isdir(source):
        write(os.path.join(work, "source-tree.txt"), os.path.abspath(source) + "\n")
    return work


def claude_command(prompt_text, claude_bin, mcp_config=None):
    cmd = [claude_bin, "-p", prompt_text, "--dangerously-skip-permissions", "--disallowedTools", DISALLOWED]
    if mcp_config:
        cmd += ["--mcp-config", mcp_config, "--strict-mcp-config"]
    return cmd


def claude_env(base, config_dir=None):
    env = dict(base)
    env["DBDOG_OBS_REPORT_URL"] = ""   # 分析会话不上报,免得评测自己再长一棵树
    env["DBDOG_OBS_TAGS"] = ""
    if config_dir:
        env["CLAUDE_CONFIG_DIR"] = config_dir
    return env


def find_claude():
    for name in ("claude", "claude.cmd", "claude.exe"):
        p = shutil.which(name)
        if p:
            return p
    raise SystemExit("找不到 claude 命令(Claude Code CLI),请先安装并确认在 PATH 里")


def resolve(a):
    ph, gt, out, fix = a.phenomenon, a.root_cause, a.out, a.fix
    if a.case_dir:
        if not os.path.isdir(a.case_dir):
            raise SystemExit(f"不是目录:{a.case_dir}")
        ph = ph or os.path.join(a.case_dir, "prompt.txt")
        if not gt:
            pure = os.path.join(a.case_dir, "root-cause.md")
            gt = pure if os.path.isfile(pure) else os.path.join(a.case_dir, "ground-truth.md")
        out = out or a.case_dir
        if not fix and os.path.isfile(os.path.join(a.case_dir, "fix.diff")):
            fix = os.path.join(a.case_dir, "fix.diff")
        if not a.window and os.path.isfile(os.path.join(a.case_dir, "window.txt")):
            a.window = read(os.path.join(a.case_dir, "window.txt")).strip()
    if not (ph and os.path.isfile(ph)):
        raise SystemExit("需要 --phenomenon <现象/题面文件>")
    if not (gt and os.path.isfile(gt)):
        raise SystemExit("需要 --root-cause <根因文件>")
    if not out:
        raise SystemExit("需要 --out <输出目录>")
    return ph, gt, out, fix, (a.source or os.environ.get("EVIDENCE_SOURCE_TREE") or "")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="run.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("case_dir", nargs="?", help="题目目录(含 prompt.txt / ground-truth.md / 可选 fix.diff)")
    ap.add_argument("--phenomenon", "--prompt", dest="phenomenon", help="现象/题面文件(正向诊断用的提示词)")
    ap.add_argument("--root-cause", "--gt", dest="root_cause", help="根因文件(标准答案)")
    ap.add_argument("--window", help="事故窗/用例执行时间,原样交给推导角当查询窗(如 \"2026-09-09 09:04–09:07 UTC+8\");不给则从题目目录 window.txt 读")
    ap.add_argument("--fix", help="修复 diff:本地文件,或 GitHub/Gitee 的 PR / commit 链接")
    ap.add_argument("--mcp-config", help="dbdog MCP 配置 JSON(显式挂;不给则继承 config dir 里已配的 MCP)")
    ap.add_argument("--source", help="被测版本内核源码树目录(代码路径核实用)")
    ap.add_argument("--out", help="输出目录")
    ap.add_argument("--config-dir", help="CLAUDE_CONFIG_DIR(选模型档)")
    ap.add_argument("--model", help="传给 claude --model")
    ap.add_argument("--force", action="store_true", help="已有产物也重跑")
    a = ap.parse_args(argv)

    ph, gt, out, fix, source = resolve(a)
    os.makedirs(out, exist_ok=True)
    target = os.path.join(out, OUT_MD)
    if os.path.isfile(target) and os.path.getsize(target) > 0 and not a.force:
        log(f"已在:{target}(--force 重跑)")
        return target
    fix_text = load_fix(fix)
    log("有修复 diff,先读 diff 再核源码" if fix_text else "无修复 diff,可信度按提示词规则降一档")
    if source and os.path.isdir(source):
        log(f"可读源码树:{source}")
    else:
        log("⚠ 没有源码树(--source / EVIDENCE_SOURCE_TREE),代码路径只能猜,verified 全为 false")
    if not a.window:
        log("⚠ 没有事故窗(--window),推导角只能按题面里的时间取证")
    work = prepare_workdir(ph, gt, fix_text, source, window=a.window)
    claude_bin = find_claude()
    cfg = a.config_dir or os.environ.get("EVIDENCE_CONFIG_DIR") or None
    log(f"claude={claude_bin} · 模型档 {cfg or '<claude 默认>'} · 工作目录 {work}")
    cmd = claude_command(read(PROMPT), claude_bin, a.mcp_config) + (["--model", a.model] if a.model else [])
    log("MCP:" + (a.mcp_config if a.mcp_config else "继承 config dir 已配的"))
    with open(os.path.join(work, "claude.stdout"), "w", encoding="utf-8") as so, \
         open(os.path.join(work, "claude.err"), "w", encoding="utf-8") as se:
        rc = subprocess.run(cmd, cwd=work, env=claude_env(os.environ, cfg), stdin=subprocess.DEVNULL,
                            stdout=so, stderr=se).returncode
    if rc != 0:
        log(f"⚠ claude 退出码 {rc},看 {os.path.join(work, 'claude.err')}")
    md, js = os.path.join(work, OUT_MD), os.path.join(work, OUT_JSON)
    if not (os.path.isfile(md) and os.path.getsize(md) > 0):
        raise SystemExit(f"没产出 {OUT_MD}(看 {work} 里的 claude.err / claude.stdout)")
    shutil.copyfile(md, target)
    if os.path.isfile(js):
        try:
            with open(js, encoding="utf-8") as f:
                json.load(f)
            shutil.copyfile(js, os.path.join(out, OUT_JSON))
            sys.path.insert(0, HERE)
            import check_chain
            log("校验 " + OUT_JSON)
            check_chain.main([os.path.join(out, OUT_JSON)])
        except ValueError:
            log(f"⚠ {OUT_JSON} 不是合法 JSON,只保留 md")
    log(f"✓ → {target}")
    return target


if __name__ == "__main__":
    main()
