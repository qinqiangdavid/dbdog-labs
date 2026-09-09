#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""evidence-chain 批次:一个 batch.md 描述全量用例,顺序逐个反向推导,用例之间歇 N 分钟,最后写 batch-summary.md。

  python batch.py batch.md [--dry-run] [--force] [--only DTS001,DTS002]

batch.md 格式(标题任意):
  - 现象文件: 多用例的复现/现象文件(按用例号找小节)
  - 根因文件: 多用例的根因文件(按用例号找小节;找不到的用例跳过)
  - 源码树: 内核源码树目录
  - 输出目录: 每个用例一个子目录
  - 间隔分钟: 用例之间的间隔(默认 5)
  - 模型 / 模型档 / MCP配置: 可选,透传给 run.py 的 --model / --config-dir / --mcp-config
  - 阶段: 缺省「正向,反向」;写「诊断,正向,反向」则先起正向诊断会话(带 hook,span 落到用例目录)再出图再反向
  - 诊断模型档 / 诊断MCP配置: 诊断会话用的模型档与 MCP(缺省同上面的)
  - 问题单地址模板: 如 https://dts.example.com/issue/{id},按用例号拼出问题单网页,skill 去页面里找修复代码/代码链接
  - 问题单请求头文件: 可选,每行 "Header: value"(如 Cookie),抓需要登录的问题单页面用
  - 用例号正则: 可选,缺省认 DTS 单号 / OG-数字 / 大写字母-数字
  | 用例 | 事故窗 | 修复 | 备注 |      表可省略:省略时用根因文件里出现的全部用例号;事故窗不填就从复现小节里解析;修复不填就按问题单模板拼
可选列「现象文件」「根因文件」按行覆盖全局文件;「span 文件」指向已有的 spans.jsonl(不填则用 用例目录/spans.jsonl)。
用例目录 = 各阶段的契约:prompt.txt / root-cause.md / window.txt → spans.jsonl(诊断) → forward-path.md(正向) → evidence-chain.md(反向)。
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
KEYS = {
    "现象文件": "phenomenon_file", "根因文件": "root_cause_file", "源码树": "source", "输出目录": "out",
    "间隔分钟": "interval_min", "模型": "model", "模型档": "config_dir", "MCP配置": "mcp_config", "mcp配置": "mcp_config",
    "阶段": "stages", "诊断模型档": "diag_config_dir", "诊断MCP配置": "diag_mcp_config", "诊断mcp配置": "diag_mcp_config",
    "问题单地址模板": "ticket_template", "问题单请求头文件": "ticket_headers_file", "用例号正则": "id_pattern",
}
COLS = {"用例": "id", "事故窗": "window", "修复": "fix", "备注": "note", "现象文件": "phenomenon_file", "根因文件": "root_cause_file",
        "span文件": "spans", "span 文件": "spans", "spans": "spans"}
SPAN_GRAPH = os.path.join(os.path.dirname(os.path.dirname(HERE)), "span-graph", "scripts", "from_spans.py")
RULES = os.path.join(os.path.dirname(os.path.dirname(HERE)), "span-graph", "references", "hypothesis-rules.txt")
STAGES_DEFAULT = "正向,反向"
ID_PATTERN_DEFAULT = r"\b(?:DTS\d{6,}|OG-\d+|[A-Z]{2,}[-_]?\d{3,})\b"
TS = r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?|\d{1,2}:\d{2}(?::\d{2})?"
WINDOW_KEYS = r"复现时间|执行时间|事故窗|时间窗|发生时间|时间段|时间范围|窗口|window|时间"


def log(msg):
    print(f"[batch] {msg}", file=sys.stderr)


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def parse_manifest(text):
    settings, cases, header = {}, [], None
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r"^[-*]\s*([^:：]+)\s*[:：]\s*(.*)$", line)
        if m and m.group(1).strip() in KEYS:
            settings[KEYS[m.group(1).strip()]] = m.group(2).strip()
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if header is None:
                header = [COLS.get(c, c) for c in cells]
                continue
            if all(re.match(r"^:?-+:?$", c) for c in cells if c):
                continue
            row = {header[i]: cells[i] if i < len(cells) else "" for i in range(len(header))}
            if row.get("id"):
                cases.append({"id": row["id"], "window": row.get("window", ""), "fix": row.get("fix", ""),
                              "note": row.get("note", ""), "phenomenon_file": row.get("phenomenon_file", ""),
                              "root_cause_file": row.get("root_cause_file", ""), "spans": row.get("spans", "")})
    try:
        settings["interval_min"] = float(settings.get("interval_min", "5") or 5)
    except ValueError:
        settings["interval_min"] = 5.0
    return {"settings": settings, "cases": cases}


def discover_ids(text, pattern=ID_PATTERN_DEFAULT):
    """从多用例文件的标题行里收集用例号(按出现顺序去重);没有标题行就扫全文。"""
    rx = re.compile(pattern)
    ids, seen = [], set()
    heads = [l for l in text.splitlines() if re.match(r"^#+\s", l)]
    for line in heads or text.splitlines():
        for m in rx.findall(line):
            if m not in seen:
                seen.add(m); ids.append(m)
    return ids


def extract_window(section):
    """从复现小节里找复现时间:优先带时间关键词的行,其次一行里有两个时间戳的行。找不到返回 ''。"""
    ts = re.compile(TS)
    cand = None
    for line in section.splitlines():
        if re.match(r"^#+\s", line):
            continue
        if re.search(WINDOW_KEYS, line, re.I) and ts.search(line):
            v = re.sub(r"^[\s\-*|]*(?:" + WINDOW_KEYS + r")[^:：\d]{0,6}[:：]?\s*", "", line, flags=re.I).strip(" |")
            return v or line.strip()
        if cand is None and len(ts.findall(line)) >= 2:
            cand = line.strip(" |-*")
    return cand or ""


def extract_section(text, case_id, all_ids):
    """按用例号切小节:优先标题行(# 开头且含用例号,取到下一个同级或更高级标题);
    没有标题就退到"含用例号的行起,到下一个含别的用例号的行止"。找不到返回 None。"""
    lines = text.splitlines()
    others = [i for i in all_ids if i != case_id]
    for n, line in enumerate(lines):
        m = re.match(r"^(#+)\s*(.*)$", line)
        if m and case_id in m.group(2):
            level = len(m.group(1))
            end = len(lines)
            for k in range(n + 1, len(lines)):
                m2 = re.match(r"^(#+)\s", lines[k])
                if m2 and len(m2.group(1)) <= level:
                    end = k
                    break
            return "\n".join(lines[n:end]).strip() + "\n"
    for n, line in enumerate(lines):
        if case_id in line:
            end = len(lines)
            for k in range(n + 1, len(lines)):
                if any(o in lines[k] for o in others):
                    end = k
                    break
            return "\n".join(lines[n:end]).strip() + "\n"
    return None


def fix_kind(fix):
    fix = (fix or "").strip()
    if not fix:
        return "none"
    if re.match(r"^https?://", fix):
        if re.search(r"\.(diff|patch)$", fix) or re.search(r"(github\.com|gitee\.com)/[^/]+/[^/]+/(pull|pulls|commit)/", fix):
            return "diff_url"
        return "ticket"
    return "file"


def build_diag_prompt(phenomenon, window, rules_text):
    """正向诊断的完整提示词 = 「诊断:」+ 题面({{WINDOW}} 用事故窗的时间段替换)+ 假设书写约定(hook 按它打 tag,缺了正向图就空)。"""
    # 切出来的小节可能带 markdown 标题行(## 用例号 …),那是文件组织,不是题面,去掉
    lines = [l for l in phenomenon.strip().splitlines()]
    while lines and re.match(r"^#+\s", lines[0]):
        lines.pop(0)
    p = "\n".join(lines).strip()
    if window:
        head = window.split(",")[0].strip()
        bare = re.sub(r"\s*\(UTC[^)]*\)\s*$", "", head)
        p = re.sub(r"\{\{WINDOW\}\}(?=\s*\(UTC)", bare, p)   # 题面自带 (UTC+8) 就不重复
        p = p.replace("{{WINDOW}}", head)
    if not re.match(r"^\s*(诊断|diag)\s*[:：]", p, re.I):
        p = "诊断: " + p
    return p + "\n\n" + rules_text.strip() + "\n"


def diag_env(base, cdir, case_id, config_dir=None):
    """诊断会话的环境:span 直接落到用例目录,hook 状态也隔离到用例目录,span 打上 case_id 标签。"""
    env = dict(base)
    env["DBDOG_OBS_SPANS"] = os.path.join(cdir, "spans.jsonl")
    env["DBDOG_OBS_DIR"] = os.path.join(cdir, "obs-state")
    env["DBDOG_OBS_TAGS"] = f"case_id={case_id}"
    env["DBDOG_OBS_ML_APP"] = env.get("DBDOG_OBS_ML_APP") or "evidence-pipeline"
    if config_dir:
        env["CLAUDE_CONFIG_DIR"] = config_dir
    return env


def diag_settings(out_dir):
    """诊断会话对答案盲:禁读整个输出目录(root-cause.md / evidence-chain.md 都在里面)与联网。"""
    root = os.path.abspath(out_dir).replace("\\", "/")
    # Claude Code 只按 Read(path) 规则做文件权限检查(它覆盖所有读文件的工具,含 Grep/Glob);Grep()/Glob() 写了也不生效
    return {"permissions": {"deny": ["WebSearch", "WebFetch", f"Read(//{root}/**)", "Bash(curl:*)", "Bash(wget:*)", "Bash(ssh:*)", "Bash(scp:*)"]}}


def diagnose(cid, cdir, phenomenon, window, st, absp):
    """阶段「诊断」:起一个带 hook + dbdog MCP 的 claude -p 跑正向诊断,span 落到 cdir/spans.jsonl。"""
    import shutil, subprocess
    wd = os.path.join(cdir, "work-diag")
    os.makedirs(wd, exist_ok=True)
    rules = read(RULES) if os.path.isfile(RULES) else ""
    if not rules:
        log(f"{cid}:⚠ 找不到假设书写约定 {RULES},正向图会没有假设 tag")
    prompt = build_diag_prompt(phenomenon, window, rules)
    write(os.path.join(wd, "prompt.used.txt"), prompt)
    sp = os.path.join(wd, "settings.json")
    write(sp, json.dumps(diag_settings(os.path.dirname(cdir)), ensure_ascii=False, indent=1))
    claude_bin = next((shutil.which(n) for n in ("claude", "claude.cmd", "claude.exe") if shutil.which(n)), None)
    if not claude_bin:
        raise SystemExit("找不到 claude 命令")
    cmd = [claude_bin, "-p", prompt, "--dangerously-skip-permissions", "--settings", sp, "--disallowedTools", "WebSearch,WebFetch"]
    mcp = st.get("diag_mcp_config") or st.get("mcp_config")
    if mcp:
        cmd += ["--mcp-config", absp(mcp), "--strict-mcp-config"]
    cwd = absp(st["source"]) if st.get("source") and os.path.isdir(absp(st["source"])) else cdir
    env = diag_env(os.environ, cdir, cid, st.get("diag_config_dir") or st.get("config_dir") or None)
    log(f"{cid}:诊断 · cwd={cwd} · span→{env['DBDOG_OBS_SPANS']} · 模型档 {env.get('CLAUDE_CONFIG_DIR') or '<默认>'}")
    with open(os.path.join(wd, "diag.stdout"), "w", encoding="utf-8") as so, open(os.path.join(wd, "diag.err"), "w", encoding="utf-8") as se:
        rc = subprocess.run(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=so, stderr=se).returncode
    if rc != 0:
        log(f"{cid}:⚠ 诊断会话退出码 {rc},看 {wd}/diag.err")
    ok = os.path.isfile(env["DBDOG_OBS_SPANS"]) and os.path.getsize(env["DBDOG_OBS_SPANS"]) > 0
    log(f"{cid}:诊断 " + ("✓ span 已落盘" if ok else "✗ 没有 span(hook 没装?触发词?看 diag.err)"))
    return ok


def forward(cid, cdir, spans_path):
    import subprocess
    if not (os.path.isfile(spans_path) and os.path.isfile(SPAN_GRAPH)):
        log(f"{cid}:⚠ span 文件或 span-graph skill 不在({spans_path}),跳过正向图")
        return False
    r = subprocess.run([sys.executable, SPAN_GRAPH, spans_path, "--out", cdir], capture_output=True, text=True)
    log(f"{cid}:正向图 " + ("✓" if r.returncode == 0 else "✗ " + r.stderr.strip()[-200:]))
    return r.returncode == 0


def summarize(out_dir, rows):
    lines = ["# 反向推导批次汇总", "", f"- 生成时间:{time.strftime('%Y-%m-%d %H:%M:%S')}", f"- 用例数:{len(rows)}", "",
             "| 用例 | 状态 | 讲不讲得通 | 符合 | 不符 | 空/报错 | 无工具 | dbdog 侧发现 | 反向产物 | 正向图 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    js_rows = []
    status_zh = {"done": "完成", "dry_run": "只备好输入", "skipped_no_root_cause": "跳过:根因文件里没有该用例",
                 "skipped_no_phenomenon": "跳过:现象文件里没有该用例", "skipped_existing": "已有产物,跳过", "failed": "失败"}
    for r in rows:
        js = os.path.join(out_dir, r["id"], "evidence-chain.json")
        verdict = ""; cnt = {"obtained_match": "", "obtained_mismatch": "", "empty_or_error": "", "no_tool": ""}; nf = ""
        if os.path.isfile(js):
            try:
                d = json.load(open(js, encoding="utf-8"))
                verdict = (d.get("consistency") or {}).get("verdict") or ""
                for e in d.get("evidence_chain") or []:
                    oc = e.get("outcome")
                    if oc in cnt:
                        cnt[oc] = (cnt[oc] or 0) + 1
                nf = len(d.get("dbdog_findings") or [])
            except ValueError:
                verdict = "json 不合法"
        md = os.path.join(out_dir, r["id"], "evidence-chain.md")
        fp = os.path.join(out_dir, r["id"], "forward-path.md")
        lines.append(f"| {r['id']} | {status_zh.get(r['status'], r['status'])}{(':' + r['detail']) if r.get('detail') else ''} | {verdict} | "
                     f"{cnt['obtained_match']} | {cnt['obtained_mismatch']} | {cnt['empty_or_error']} | {cnt['no_tool']} | {nf} | "
                     f"{'evidence-chain.md' if os.path.isfile(md) else ''} | {'forward-path.md' if os.path.isfile(fp) else ''} |")
        js_rows.append({"id": r["id"], "status": r["status"], "detail": r.get("detail", ""), "verdict": verdict,
                        "outcomes": {k: (v or 0) for k, v in cnt.items()}, "dbdog_findings": nf or 0,
                        "evidence_chain_md": md if os.path.isfile(md) else None, "forward_path_md": fp if os.path.isfile(fp) else None})
    write(os.path.join(out_dir, "batch-summary.md"), "\n".join(lines) + "\n")
    write(os.path.join(out_dir, "batch-summary.json"), json.dumps({"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "cases": js_rows}, ensure_ascii=False, indent=2) + "\n")


def check(manifest_path):
    """自检:环境四样 + batch.md 能解析 + 每个用例能切到现象/根因。返回问题列表(空=可以跑)。"""
    import shutil, urllib.request
    problems, notes = [], []
    if sys.version_info < (3, 8):
        problems.append(f"Python 需要 3.8+,当前 {sys.version.split()[0]}")
    cl = next((shutil.which(n) for n in ("claude", "claude.cmd", "claude.exe") if shutil.which(n)), None)
    (notes if cl else problems).append(f"claude CLI:{cl or '找不到,请安装 Claude Code 并加入 PATH'}")
    try:
        m = parse_manifest(read(manifest_path))
    except Exception as e:
        return [f"batch.md 读不了:{e}"], notes
    st, cases = m["settings"], m["cases"]
    base = os.path.dirname(os.path.abspath(manifest_path))
    def absp(p):
        return os.path.abspath(os.path.join(base, p)) if p and not os.path.isabs(p) else p
    for k, zh in (("phenomenon_file", "现象文件"), ("root_cause_file", "根因文件")):
        if not st.get(k):
            notes.append(f"{zh}:未配置,每行要自带「{zh}」列")
        elif not os.path.isfile(absp(st[k])):
            problems.append(f"{zh}不存在:{absp(st[k])}")
    if st.get("source") and not os.path.isdir(absp(st["source"])):
        problems.append(f"源码树不存在:{absp(st['source'])}")
    elif not st.get("source"):
        notes.append("源码树:未配置,代码路径将全部 verified=false")
    if st.get("mcp_config"):
        mp = absp(st["mcp_config"])
        if not os.path.isfile(mp):
            problems.append(f"MCP配置不存在:{mp}")
        else:
            try:
                cfg = json.load(open(mp, encoding="utf-8"))
                for name, srv in (cfg.get("mcpServers") or {}).items():
                    url = srv.get("url")
                    if not url:
                        continue
                    req = urllib.request.Request(url, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "evidence-chain-check", "version": "0"}}}).encode(),
                                                 headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **(srv.get("headers") or {})}, method="POST")
                    try:
                        with urllib.request.urlopen(req, timeout=20) as r:
                            notes.append(f"MCP {name}:HTTP {r.status} 可达")
                    except Exception as e:
                        problems.append(f"MCP {name} 连不上:{url} ({e})")
            except ValueError as e:
                problems.append(f"MCP配置不是合法 JSON:{e}")
    else:
        notes.append("MCP配置:未指定,将继承 claude 配置目录里已配的 MCP(确认里面有 dbdog)")
    stages = st.get("stages") or STAGES_DEFAULT
    notes.append(f"阶段:{stages}" + ("(含诊断:需要 hook 已装、假设书写约定 " + ("在" if os.path.isfile(RULES) else "缺失!") + ")" if "诊断" in stages else ""))
    if "正向" in stages and not os.path.isfile(SPAN_GRAPH):
        problems.append(f"span-graph skill 不在:{SPAN_GRAPH}(正向图要它)")
    ph_all = read(absp(st["phenomenon_file"])) if st.get("phenomenon_file") and os.path.isfile(absp(st["phenomenon_file"])) else ""
    rc_all = read(absp(st["root_cause_file"])) if st.get("root_cause_file") and os.path.isfile(absp(st["root_cause_file"])) else ""
    if not cases:
        cases = [{"id": i} for i in discover_ids(rc_all or ph_all, st.get("id_pattern") or ID_PATTERN_DEFAULT)]
        notes.append(f"用例表未填,从{'根因' if rc_all else '现象'}文件发现 {len(cases)} 个用例:{', '.join(c['id'] for c in cases) or '无'}")
        if not cases:
            problems.append("既没有用例表,也没能从文件里发现用例号(可用「用例号正则」指定)")
    if st.get("ticket_template"):
        notes.append(f"问题单地址模板:{st['ticket_template']}" + ("" if "{id}" in st["ticket_template"] else " ← 缺 {id} 占位!"))
        if "{id}" not in st["ticket_template"]:
            problems.append("问题单地址模板里要有 {id} 占位")
    if st.get("ticket_headers_file") and not os.path.isfile(absp(st["ticket_headers_file"])):
        problems.append(f"问题单请求头文件不存在:{absp(st['ticket_headers_file'])}")
    ids = [c["id"] for c in cases]
    for c in cases:
        cid = c["id"]
        sec = extract_section(ph_all, cid, ids) if not c.get("phenomenon_file") else read(absp(c["phenomenon_file"]))
        if not sec:
            problems.append(f"{cid}:现象文件里切不到该用例")
        if not (c.get("root_cause_file") or extract_section(rc_all, cid, ids)):
            notes.append(f"{cid}:根因文件里没有,将跳过")
        win = c.get("window") or (extract_window(sec) if sec else "")
        notes.append(f"{cid}:事故窗 " + (f"「{win}」" if win else "没抓到 ← 复现小节里要有带时间的行(复现时间/执行时间/时间窗…)"))
        if not c.get("fix") and st.get("ticket_template"):
            c["fix"] = st["ticket_template"].replace("{id}", cid)
        k = fix_kind(c.get("fix"))
        if k == "file" and not os.path.isfile(absp(c["fix"])):
            problems.append(f"{cid}:修复 diff 文件不存在:{absp(c['fix'])}")
        if k == "none":
            notes.append(f"{cid}:没有修复来源,按 fix_diff: absent")
    return problems, notes


def run_batch(manifest_path, dry_run=False, force=False, only=None):
    m = parse_manifest(read(manifest_path))
    st, cases = m["settings"], m["cases"]
    base = os.path.dirname(os.path.abspath(manifest_path))
    def absp(p):
        return os.path.abspath(os.path.join(base, p)) if p and not os.path.isabs(p) else p
    out_dir = absp(st.get("out") or os.path.join(base, "out"))
    os.makedirs(out_dir, exist_ok=True)
    ph_all = read(absp(st["phenomenon_file"])) if st.get("phenomenon_file") else ""
    rc_all = read(absp(st["root_cause_file"])) if st.get("root_cause_file") else ""
    if not cases:
        cases = [{"id": i, "window": "", "fix": "", "note": "", "phenomenon_file": "", "root_cause_file": "", "spans": ""}
                 for i in discover_ids(rc_all or ph_all, st.get("id_pattern") or ID_PATTERN_DEFAULT)]
        log(f"batch.md 没有用例表,从{'根因' if rc_all else '现象'}文件里发现 {len(cases)} 个用例:{', '.join(c['id'] for c in cases)}")
    ids = [c["id"] for c in cases]
    if only:
        cases = [c for c in cases if c["id"] in only]
    sys.path.insert(0, HERE)
    import run as ec
    rows = []
    for i, c in enumerate(cases):
        cid = c["id"]
        cdir = os.path.join(out_dir, cid)
        os.makedirs(cdir, exist_ok=True)
        row = {"id": cid, "status": "", "detail": ""}
        rows.append(row)
        ph = read(absp(c["phenomenon_file"])) if c.get("phenomenon_file") else extract_section(ph_all, cid, ids)
        if not ph:
            row["status"] = "skipped_no_phenomenon"; log(f"{cid}:现象文件里没找到该用例,跳过"); continue
        rc = read(absp(c["root_cause_file"])) if c.get("root_cause_file") else extract_section(rc_all, cid, ids)
        if not rc:
            row["status"] = "skipped_no_root_cause"; log(f"{cid}:根因文件里没找到该用例,跳过"); continue
        if not c.get("window"):
            c["window"] = extract_window(ph)
            log(f"{cid}:复现时间 " + (f"← 现象文件「{c['window']}」" if c["window"] else "没抓到(推导角只能按题面里的时间取证)"))
        if not c.get("fix") and st.get("ticket_template"):
            c["fix"] = st["ticket_template"].replace("{id}", cid)
        write(os.path.join(cdir, "prompt.txt"), ph)
        write(os.path.join(cdir, "root-cause.md"), rc)
        write(os.path.join(cdir, "window.txt"), (c.get("window") or "").strip() + "\n")
        stages = [x.strip() for x in re.split(r"[,，\s]+", st.get("stages") or STAGES_DEFAULT) if x.strip()]
        spans_path = absp(c["spans"]) if c.get("spans") else os.path.join(cdir, "spans.jsonl")
        planned = []
        if "诊断" in stages and (force or not (os.path.isfile(spans_path) and os.path.getsize(spans_path) > 0)):
            planned.append("诊断")
        if "正向" in stages:
            planned.append("正向")
        if "反向" in stages and (force or not os.path.isfile(os.path.join(cdir, "evidence-chain.md"))):
            planned.append("反向")
        row["stages"] = planned
        if not planned:
            row["status"] = "skipped_existing"; log(f"{cid}:各阶段产物都在,跳过(--force 重跑)"); continue
        args = ["--phenomenon", os.path.join(cdir, "prompt.txt"), "--root-cause", os.path.join(cdir, "root-cause.md"),
                "--out", cdir, "--work", os.path.join(cdir, "work")]
        if c.get("window"):
            args += ["--window", c["window"]]
        if st.get("source"):
            args += ["--source", absp(st["source"])]
        kind = fix_kind(c.get("fix"))
        if kind == "file":
            args += ["--fix", absp(c["fix"])]
        elif kind in ("diff_url", "ticket"):
            args += ["--fix", c["fix"]]
        for k, flag in (("model", "--model"), ("config_dir", "--config-dir"), ("mcp_config", "--mcp-config"),
                        ("ticket_headers_file", "--ticket-headers")):
            if st.get(k):
                args += [flag, absp(st[k]) if k != "model" else st[k]]
        if force:
            args.append("--force")
        log(f"{cid}:修复来源={kind} · 窗={c.get('window') or '无'}")
        if dry_run:
            write(os.path.join(cdir, "dry-run.txt"), "阶段: " + " → ".join(planned) + "\npython run.py " + " ".join(args) + "\n")
            row["status"] = "dry_run"; continue
        try:
            if "诊断" in planned:
                diagnose(cid, cdir, ph, c.get("window"), st, absp)
            if "正向" in planned:
                forward(cid, cdir, spans_path)
            if "反向" in planned:
                ec.main(args)
            row["status"] = "done"; row["detail"] = "+".join(planned)
        except SystemExit as e:
            row["status"] = "failed"; row["detail"] = str(e)
        except Exception as e:  # 一个用例失败不拖累整批
            row["status"] = "failed"; row["detail"] = f"{type(e).__name__}: {e}"
        summarize(out_dir, rows)
        remaining = [x for x in cases[i + 1:]]
        if remaining and st["interval_min"] > 0:
            log(f"歇 {st['interval_min']} 分钟再跑下一个({remaining[0]['id']})")
            time.sleep(st["interval_min"] * 60)
    summarize(out_dir, rows)
    log(f"汇总 → {os.path.join(out_dir, 'batch-summary.md')}")
    return rows


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="batch.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", help="batch.md")
    ap.add_argument("--dry-run", action="store_true", help="只切好每个用例的输入、写汇总,不起模型")
    ap.add_argument("--force", action="store_true", help="已有产物也重跑")
    ap.add_argument("--only", help="只跑这些用例号,逗号分隔")
    ap.add_argument("--check", action="store_true", help="只做自检:环境、batch.md、每个用例能否切到,不跑")
    a = ap.parse_args(argv)
    if a.check:
        problems, notes = check(a.manifest)
        for n in notes:
            print("· " + n)
        for p in problems:
            print("✗ " + p)
        print("自检:" + ("可以跑" if not problems else f"{len(problems)} 处要先修"))
        sys.exit(1 if problems else 0)
    rows = run_batch(a.manifest, a.dry_run, a.force, set(a.only.split(",")) if a.only else None)
    sys.exit(1 if any(r["status"] == "failed" for r in rows) else 0)


if __name__ == "__main__":
    main()
