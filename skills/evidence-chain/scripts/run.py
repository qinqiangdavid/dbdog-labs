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
from html.parser import HTMLParser

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


def case_md(phenomenon_file, root_cause_file, window=None, ticket_url=None, ticket_text=None):
    out = ["# 反向输入", ""]
    if ticket_url:
        out += ["## 修复来源(问题单)", "", f"修复代码在这个问题单网页里(或页面里给的 commit / PR 链接指向的代码):{ticket_url}",
                ("其正文已抓成 `ticket.txt` 放在当前目录,先读它找修复代码/补丁;" if ticket_text else
                 "本机抓不到该页面(可能要登录),请用 WebFetch 打开这个地址读修复代码/补丁;"),
                "页面里若只给了指向代码的链接(commit / PR / MR / gerrit),沿链接用 WebFetch 把 diff 取下来;",
                "当前目录已有 `fix.diff` 就直接用它;都找不到修复代码就按 `fix_diff: absent` 处理。", ""]
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


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self._skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"): self._skip += 1
    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip: self._skip -= 1
        if tag in ("p", "div", "br", "li", "tr", "pre", "h1", "h2", "h3", "h4"): self.parts.append("\n")
    def handle_data(self, data):
        if not self._skip: self.parts.append(data)


def html_to_text(html):
    t = _Text(); t.feed(html)
    return re.sub(r"\n{3,}", "\n\n", "".join(t.parts)).strip()


def is_ticket_url(fix):
    return bool(re.match(r"^https?://", fix or "")) and not re.search(r"\.(diff|patch)$", fix) \
        and not re.search(r"(github\.com|gitee\.com)/[^/]+/[^/]+/(pull|pulls|commit)/", fix)


CODE_LINK = re.compile(r"""href=["']([^"']+?(?:/commit/|/commits/|/pull/|/pulls/|/merge_requests/|/changes/|/compare/|\.diff|\.patch)[^"']*)["']""", re.I)


def load_headers(path):
    h = {"User-Agent": "evidence-chain"}
    if path and os.path.isfile(path):
        for line in read(path).splitlines():
            if ":" in line and not line.strip().startswith("#"):
                k, v = line.split(":", 1)
                h[k.strip()] = v.strip()
    return h


def fetch(url, headers, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def find_code_links(html, base_url=""):
    """问题单页面里指向代码的链接(commit / PR / MR / gerrit change / .diff / .patch),去重保序。"""
    from urllib.parse import urljoin
    out, seen = [], set()
    for m in CODE_LINK.findall(html):
        u = urljoin(base_url, m.replace("&amp;", "&"))
        if u not in seen:
            seen.add(u); out.append(u)
    return out


def load_ticket(url, headers_file=None):
    """问题单网页(如 DTS):能下载就转成文本,并顺着页面里的代码链接把 diff 扒下来。
    返回 (页面文本或 None, diff 文本或 None, 代码链接列表)。下载不了(要登录)→ (None, None, []),由推导角自己打开。"""
    headers = load_headers(headers_file)
    try:
        body = fetch(url, headers)
    except Exception:
        return None, None, []
    is_html = "<" in body[:2000]
    text = html_to_text(body) if is_html else body
    links = find_code_links(body, url) if is_html else []
    diff = None
    for link in links[:5]:
        cand = diff_url(link) or link
        try:
            d = fetch(cand, headers, timeout=60)
            if re.search(r"^(diff --git|--- |\+\+\+ |@@ )", d, re.M):
                diff = d
                log(f"从问题单里的代码链接取到 diff:{cand}")
                break
        except Exception:
            continue
    if diff is None and re.search(r"^(diff --git|@@ )", text, re.M):
        diff = text   # 页面正文本身贴了补丁
    return (text if len(text.strip()) > 200 else None), diff, links


def load_fix(fix):
    if not fix:
        return None
    if is_ticket_url(fix):
        return None   # 问题单网页走 ticket 通道,见 main
    url = diff_url(fix)
    if url:
        log(f"下载修复 diff:{url}")
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "evidence-chain"}), timeout=60) as r:
            return r.read().decode("utf-8", "replace")
    if not os.path.isfile(fix):
        raise SystemExit(f"找不到修复 diff:{fix}")
    return read(fix)


def prepare_workdir(phenomenon, root_cause, fix_text, source, work=None, window=None, ticket_url=None, ticket_text=None):
    work = work or tempfile.mkdtemp(prefix="evidence-chain-")
    os.makedirs(work, exist_ok=True)
    write(os.path.join(work, "case.md"), case_md(phenomenon, root_cause, window, ticket_url, ticket_text))
    if ticket_text:
        write(os.path.join(work, "ticket.txt"), ticket_text)
    shutil.copyfile(root_cause, os.path.join(work, "root-cause.md"))
    if fix_text:
        write(os.path.join(work, "fix.diff"), fix_text)
    shutil.copyfile(CATALOG, os.path.join(work, "tool-catalog.json"))
    if source and os.path.isdir(source):
        write(os.path.join(work, "source-tree.txt"), os.path.abspath(source) + "\n")
    return work


def claude_command(prompt_text, claude_bin, mcp_config=None, allow_webfetch=False):
    dis = DISALLOWED.replace("WebFetch,", "") if allow_webfetch else DISALLOWED
    cmd = [claude_bin, "-p", prompt_text, "--dangerously-skip-permissions", "--disallowedTools", dis]
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
    ap.add_argument("--fix", help="修复来源:本地 diff 文件、GitHub/Gitee 的 PR / commit 链接,或问题单网页地址(如 DTS 单,修复代码在页面里)")
    ap.add_argument("--mcp-config", help="dbdog MCP 配置 JSON(显式挂;不给则继承 config dir 里已配的 MCP)")
    ap.add_argument("--ticket-headers", help="抓问题单页面用的请求头文件(每行 Header: value,如 Cookie)")
    ap.add_argument("--source", help="被测版本内核源码树目录(代码路径核实用)")
    ap.add_argument("--out", help="输出目录")
    ap.add_argument("--config-dir", help="CLAUDE_CONFIG_DIR(选模型档)")
    ap.add_argument("--model", help="传给 claude --model")
    ap.add_argument("--force", action="store_true", help="已有产物也重跑")
    ap.add_argument("--work", help="推导角的工作目录(缺省系统临时目录;批次会指到 输出目录/用例号/work 让日志留下)")
    a = ap.parse_args(argv)

    ph, gt, out, fix, source = resolve(a)
    os.makedirs(out, exist_ok=True)
    target = os.path.join(out, OUT_MD)
    if os.path.isfile(target) and os.path.getsize(target) > 0 and not a.force:
        log(f"已在:{target}(--force 重跑)")
        return target
    ticket_url = fix if is_ticket_url(fix or "") else None
    ticket_text, fix_text, links = None, None, []
    if ticket_url:
        ticket_text, fix_text, links = load_ticket(ticket_url, a.ticket_headers)
        log(f"修复来源是问题单网页:{ticket_url}(" + ("已抓成 ticket.txt" if ticket_text else "抓不到,交给推导角用 WebFetch 打开")
            + (f";页面里 {len(links)} 个代码链接" if links else "") + (";已取到 diff" if fix_text else "") + ")")
    else:
        fix_text = load_fix(fix)
        log("有修复 diff,先读 diff 再核源码" if fix_text else "无修复 diff,可信度按提示词规则降一档")
    if source and os.path.isdir(source):
        log(f"可读源码树:{source}")
    else:
        log("⚠ 没有源码树(--source / EVIDENCE_SOURCE_TREE),代码路径只能猜,verified 全为 false")
    if not a.window:
        log("⚠ 没有事故窗(--window),推导角只能按题面里的时间取证")
    work = prepare_workdir(ph, gt, fix_text, source, work=a.work, window=a.window, ticket_url=ticket_url, ticket_text=ticket_text)
    claude_bin = find_claude()
    cfg = a.config_dir or os.environ.get("EVIDENCE_CONFIG_DIR") or None
    log(f"claude={claude_bin} · 模型档 {cfg or '<claude 默认>'} · 工作目录 {work}")
    cmd = claude_command(read(PROMPT), claude_bin, a.mcp_config, allow_webfetch=bool(ticket_url and not fix_text)) + (["--model", a.model] if a.model else [])
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
