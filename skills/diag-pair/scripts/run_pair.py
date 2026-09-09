#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""diag-pair 跨平台入口(Windows / macOS / Linux,只用标准库)。

  python run_pair.py reverse --prompt 题面.txt --root-cause 根因.md --source 源码树目录 [--fix fix.diff] [--out 目录]
  python run_pair.py reverse <题目目录> --source 源码树目录        # 目录里有 prompt.txt / ground-truth.md
  python run_pair.py forward <spans.jsonl | 含 spans.jsonl 的目录> [--trace ID] [--session ID] [--out 目录]

反向:组装工作目录 → 调 `claude -p`(模型读源码树推证据链)→ 产 reverse-chain.md/.json。
正向:零模型,from_spans.py 从 hook span 抽假设图 → forward-path.md/.json。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
CATALOG = os.path.join(SKILL, "references", "dbdog-tool-catalog.json")
REVERSE_PROMPT = os.path.join(SKILL, "prompts", "reverse.md")
DISALLOWED = "Bash,WebFetch,WebSearch,Agent,Task,NotebookEdit,Edit"


def log(msg):
    print(f"[pair] {msg}", file=sys.stderr)


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def case_md(prompt_file, root_cause_file):
    prompt = read(prompt_file).strip()
    out = ["# 反向输入", "", "## 题面(正向诊断用的提示词,即「诊断:」后面跟的那段)", "", prompt, ""]
    gt = read(root_cause_file)
    m = re.search(r"^##\s*现象量化[^\n]*\n(.*?)(?=^##|\Z)", gt, re.S | re.M)
    if m:
        out += ["## 现象量化(实测)", "", m.group(1).strip(), ""]
    return "\n".join(out)


def prepare_reverse_workdir(prompt, root_cause, source, fix=None, work=None):
    work = work or tempfile.mkdtemp(prefix="pair-reverse-")
    os.makedirs(work, exist_ok=True)
    write(os.path.join(work, "case.md"), case_md(prompt, root_cause))
    shutil.copyfile(root_cause, os.path.join(work, "root-cause.md"))
    if fix and os.path.isfile(fix):
        shutil.copyfile(fix, os.path.join(work, "fix.diff"))
    shutil.copyfile(CATALOG, os.path.join(work, "tool-catalog.json"))
    if source and os.path.isdir(source):
        write(os.path.join(work, "source-tree.txt"), os.path.abspath(source) + "\n")
    return work


def reverse_command(prompt_text, claude_bin="claude"):
    return [claude_bin, "-p", prompt_text, "--dangerously-skip-permissions", "--disallowedTools", DISALLOWED]


def reverse_env(base, config_dir=None):
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


def resolve_reverse_args(a):
    prompt, gt, out, fix = a.prompt, a.root_cause, a.out, a.fix
    if a.case_dir:
        if not os.path.isdir(a.case_dir):
            raise SystemExit(f"不是目录:{a.case_dir}")
        prompt = prompt or os.path.join(a.case_dir, "prompt.txt")
        gt = gt or os.path.join(a.case_dir, "ground-truth.md")
        out = out or a.case_dir
        if not fix and os.path.isfile(os.path.join(a.case_dir, "fix.diff")):
            fix = os.path.join(a.case_dir, "fix.diff")
    if not (prompt and os.path.isfile(prompt)):
        raise SystemExit("反向需要 --prompt <正向诊断用的提示词文件>")
    if not (gt and os.path.isfile(gt)):
        raise SystemExit("反向需要 --root-cause <根因文件>")
    if not out:
        raise SystemExit("反向需要 --out <输出目录>")
    source = a.source or os.environ.get("PAIR_SOURCE_TREE") or ""
    return {"prompt": prompt, "root_cause": gt, "out": out, "fix": fix, "source": source}


def do_reverse(a):
    r = resolve_reverse_args(a)
    os.makedirs(r["out"], exist_ok=True)
    target = os.path.join(r["out"], "reverse-chain.md")
    if os.path.isfile(target) and os.path.getsize(target) > 0 and not a.force:
        log(f"reverse 已在:{target}(--force 重跑)")
        return target
    if r["source"] and os.path.isdir(r["source"]):
        log(f"reverse 可读源码树:{r['source']}")
    else:
        log("⚠ 没有源码树(--source 或 PAIR_SOURCE_TREE),代码路径只能猜,verified 全为 false")
    work = prepare_reverse_workdir(r["prompt"], r["root_cause"], r["source"], r["fix"])
    if not r["fix"]:
        log("无 fix.diff,可信度按提示词规则降一档")
    claude_bin = find_claude()
    cfg = a.config_dir or os.environ.get("PAIR_CONFIG_DIR") or None
    log(f"reverse · claude={claude_bin} · 模型档 {cfg or '<claude 默认>'} · 工作目录 {work}")
    cmd = reverse_command(read(REVERSE_PROMPT), claude_bin)
    if a.model:
        cmd += ["--model", a.model]
    with open(os.path.join(work, "reverse.stdout"), "w", encoding="utf-8") as so, \
         open(os.path.join(work, "reverse.err"), "w", encoding="utf-8") as se:
        rc = subprocess.run(cmd, cwd=work, env=reverse_env(os.environ, cfg), stdin=subprocess.DEVNULL,
                            stdout=so, stderr=se).returncode
    if rc != 0:
        log(f"⚠ claude 退出码 {rc},看 {os.path.join(work, 'reverse.err')}")
    md = os.path.join(work, "reverse-chain.md")
    js = os.path.join(work, "reverse-chain.json")
    if not (os.path.isfile(md) and os.path.getsize(md) > 0):
        raise SystemExit(f"reverse 没产出 reverse-chain.md(看 {work}/reverse.err 与 reverse.stdout)")
    if os.path.isfile(js):
        try:
            json.load(open(js, encoding="utf-8"))
            shutil.copyfile(js, os.path.join(r["out"], "reverse-chain.json"))
        except ValueError:
            log("⚠ reverse-chain.json 不是合法 JSON,只保留 md")
    shutil.copyfile(md, target)
    log(f"reverse ✓ → {target}")
    return target


def do_forward(a):
    sys.path.insert(0, HERE)
    import from_spans
    return from_spans.run(a.path, a.out, a.session, a.trace)


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="run_pair.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("reverse", help="根因 → 反向推断链路(调模型,读源码)")
    r.add_argument("case_dir", nargs="?", help="题目目录(含 prompt.txt / ground-truth.md),可代替 --prompt/--root-cause/--out")
    r.add_argument("--prompt", help="正向诊断用的提示词文件(题面)")
    r.add_argument("--root-cause", "--gt", dest="root_cause", help="根因文件(标准答案)")
    r.add_argument("--source", help="被测版本内核源码树目录(代码路径核实用)")
    r.add_argument("--fix", help="修复 diff(可选)")
    r.add_argument("--out", help="输出目录")
    r.add_argument("--config-dir", help="CLAUDE_CONFIG_DIR(选模型档,可选)")
    r.add_argument("--model", help="传给 claude --model(可选)")
    r.add_argument("--force", action="store_true", help="已有 reverse-chain.md 也重跑")
    f = sub.add_parser("forward", help="hook span → 正向假设图(零模型)")
    f.add_argument("path", help="spans.jsonl / server 导出 JSON / 含 spans.jsonl 的目录")
    f.add_argument("--out", help="输出目录(默认写在输入旁)")
    f.add_argument("--trace", help="只取这个 trace_id")
    f.add_argument("--session", help="只取这个 session_id")
    return ap.parse_args(argv)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    a = parse_args(argv)
    if a.cmd == "reverse":
        do_reverse(a)
    else:
        do_forward(a)


if __name__ == "__main__":
    main()
