#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""diag-compare 单用例:读同一单号目录里的 evidence-chain.{md,json} + forward-path.{md,json}(+ forward-conclusion.md),
先零模型预匹配出 prematch.json,再起 claude -p 判六类结论,产 compare.md / compare.json。

  python run.py <单号目录> [--config-dir DIR] [--model NAME] [--force]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
PROMPT = os.path.join(SKILL, "prompts", "compare.md")
INPUTS = ("evidence-chain.md", "evidence-chain.json", "forward-path.md", "forward-path.json")
OPTIONAL = ("forward-conclusion.md",)
DISALLOWED = "Bash,WebFetch,WebSearch,Agent,Task,NotebookEdit"


def log(msg):
    print(f"[compare] {msg}", file=sys.stderr)


def find_claude():
    for n in ("claude", "claude.cmd", "claude.exe"):
        p = shutil.which(n)
        if p:
            return p
    raise SystemExit("找不到 claude 命令")


def prepare(case_dir, work):
    os.makedirs(work, exist_ok=True)
    for f in INPUTS:
        src = os.path.join(case_dir, f)
        if not os.path.isfile(src):
            raise SystemExit(f"缺 {src}(反向用 evidence-chain 出,正向用 span-graph 出)")
        shutil.copyfile(src, os.path.join(work, f))
    for f in OPTIONAL:
        src = os.path.join(case_dir, f)
        if os.path.isfile(src):
            shutil.copyfile(src, os.path.join(work, f))
    sys.path.insert(0, HERE)
    import prematch
    out, m = prematch.run(case_dir)
    shutil.copyfile(out, os.path.join(work, "prematch.json"))
    return m


def claude_env(base, config_dir=None):
    env = dict(base)
    env["DBDOG_OBS_REPORT_URL"] = ""; env["DBDOG_OBS_TAGS"] = ""
    if config_dir:
        env["CLAUDE_CONFIG_DIR"] = config_dir
    return env


def main(argv=None):
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="run.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("case_dir")
    ap.add_argument("--config-dir", help="CLAUDE_CONFIG_DIR(选模型档;对比要用强模型)")
    ap.add_argument("--model", help="传给 claude --model")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    cdir = os.path.abspath(a.case_dir)
    target = os.path.join(cdir, "compare.md")
    if os.path.isfile(target) and os.path.getsize(target) > 0 and not a.force:
        log(f"已在:{target}(--force 重跑)"); return target
    work = os.path.join(cdir, "work-compare")
    m = prepare(cdir, work)
    log(f"预匹配:证据 {m['reverse']['evidence']} · 正向调用 {m['forward']['tool_calls']} · 初判 {m['prelim_counts']}")
    claude_bin = find_claude()
    cfg = a.config_dir or os.environ.get("COMPARE_CONFIG_DIR") or None
    cmd = [claude_bin, "-p", open(PROMPT, encoding="utf-8").read(), "--dangerously-skip-permissions", "--disallowedTools", DISALLOWED]
    if a.model:
        cmd += ["--model", a.model]
    log(f"claude={claude_bin} · 模型档 {cfg or '<默认>'} · 工作目录 {work}")
    with open(os.path.join(work, "claude.stdout"), "w", encoding="utf-8") as so, open(os.path.join(work, "claude.err"), "w", encoding="utf-8") as se:
        rc = subprocess.run(cmd, cwd=work, env=claude_env(os.environ, cfg), stdin=subprocess.DEVNULL, stdout=so, stderr=se).returncode
    if rc != 0:
        log(f"⚠ claude 退出码 {rc},看 {work}/claude.err")
    md, js = os.path.join(work, "compare.md"), os.path.join(work, "compare.json")
    if not (os.path.isfile(md) and os.path.getsize(md) > 0):
        raise SystemExit(f"没产出 compare.md(看 {work}/claude.err)")
    shutil.copyfile(md, target)
    if os.path.isfile(js):
        try:
            with open(js, encoding="utf-8") as f:
                d = json.load(f)
            shutil.copyfile(js, os.path.join(cdir, "compare.json"))
            log(f"结论:{d.get('counts')} · 主要在 {d.get('main_layer')} · 改进 {len(d.get('improvements') or [])} 条")
        except ValueError:
            log("⚠ compare.json 不是合法 JSON,只保留 md")
    log(f"✓ → {target}")
    return target


if __name__ == "__main__":
    main()
