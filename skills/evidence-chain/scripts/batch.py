#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""evidence-chain 批次:一份 batch.md 指向复现文件与根因文件,按问题单号逐个反向取证,输出父目录下按单号建子目录。

  python batch.py batch.md [--check] [--dry-run] [--force] [--only ID1,ID2]

batch.md(标题任意,几行变量):
  - 复现文件: 多用例复现文件——单号、复现开始/结束时间、现象都从这里拿(每个单号一个小节,标题行含单号)
  - 根因文件: 多用例根因文件,按单号找小节;没有的单号跳过。小节里写了修复代码链接(commit / PR / diff)也会被认出来
  - 源码树: 内核源码树目录
  - 输出目录: 父目录,下面按单号建子目录:<单号>/prompt.txt · root-cause.md · window.txt · evidence-chain.md(+.json) · work/
  - 间隔分钟(缺省 5)/ MCP配置 / 模型档 / 模型:可选
  - 问题单文件: 可选,一行一个单号(+ 修复链接 + 事故窗),用来限定/覆盖复现文件里发现的单号
  - 用例号正则: 可选,缺省认 DTS 单号 / OG-数字 / 大写字母-数字
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
KEYS = {
    "复现文件": "phenomenon_file", "现象文件": "phenomenon_file", "根因文件": "root_cause_file", "源码树": "source",
    "输出目录": "out", "间隔分钟": "interval_min", "模型": "model", "模型档": "config_dir",
    "MCP配置": "mcp_config", "mcp配置": "mcp_config", "问题单文件": "tickets_file", "单号文件": "tickets_file",
    "用例号正则": "id_pattern", "问题单地址模板": "ticket_template",
}
ID_PATTERN_DEFAULT = r"\b(?:DTS\d{6,}|OG-\d+|[A-Z]{2,}[-_]?\d{3,})\b"
TS = r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?|\d{1,2}:\d{2}(?::\d{2})?"
START_KEYS = r"复现开始|开始时间|起始时间|start"
END_KEYS = r"复现结束|结束时间|终止时间|end"
WINDOW_KEYS = r"复现时间|执行时间|事故窗|时间窗|发生时间|时间段|时间范围|窗口|window|时间"
FIX_KEYS = r"修复|fix|patch|补丁|commit|PR|MR|链接|代码"
CODE_URL = re.compile(r"https?://\S+?(?:/commit/|/commits/|/pull/|/pulls/|/merge_requests/|/changes/|\.diff|\.patch)\S*", re.I)


def log(msg):
    print(f"[batch] {msg}", file=sys.stderr)


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def parse_manifest(text):
    settings = {}
    for raw in text.splitlines():
        m = re.match(r"^\s*[-*]?\s*([^:：]+?)\s*[:：]\s*(.*)$", raw.strip())
        if m and m.group(1).strip() in KEYS:
            settings[KEYS[m.group(1).strip()]] = m.group(2).strip()
    try:
        settings["interval_min"] = float(settings.get("interval_min", "5") or 5)
    except ValueError:
        settings["interval_min"] = 5.0
    return {"settings": settings}


def parse_tickets(text):
    """可选的问题单文件:一行一个单号,后面可跟修复代码链接和事故窗(空格 / | / 制表符分开);# 注释;markdown 表也认。"""
    cases = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or re.match(r"^\|?\s*:?-+", line):
            continue
        cells = [c.strip() for c in (line.strip("|").split("|") if "|" in line else (line.split("\t") if "\t" in line else line.split()))]
        cells = [c for c in cells if c]
        if not cells or cells[0] in ("用例", "单号", "问题单"):
            continue
        cid, fix, win = cells[0], "", []
        for c in cells[1:]:
            if not fix and (re.match(r"^https?://", c) or re.search(r"\.(diff|patch)$", c) or re.match(r"^[A-Za-z]:\\|^[./~]", c)):
                fix = c
            else:
                win.append(c)
        cases.append({"id": cid, "fix": fix, "window": " ".join(win).strip()})
    return cases


def discover_ids(text, pattern=ID_PATTERN_DEFAULT):
    """从多用例文件的标题行里收集单号(按出现顺序去重);没有标题行就扫全文。"""
    rx = re.compile(pattern)
    ids, seen = [], set()
    heads = [l for l in text.splitlines() if re.match(r"^#+\s", l)]
    for line in heads or text.splitlines():
        for m in rx.findall(line):
            if m not in seen:
                seen.add(m); ids.append(m)
    return ids


def extract_section(text, case_id, all_ids):
    """按单号切小节:优先标题行(# 开头且含单号,取到下一个同级或更高级标题);没有标题就从含单号的行起到下一个含别的单号的行止。"""
    lines = text.splitlines()
    others = [i for i in all_ids if i != case_id]
    for n, line in enumerate(lines):
        m = re.match(r"^(#+)\s*(.*)$", line)
        if m and case_id in m.group(2):
            level = len(m.group(1)); end = len(lines)
            for k in range(n + 1, len(lines)):
                m2 = re.match(r"^(#+)\s", lines[k])
                if m2 and len(m2.group(1)) <= level:
                    end = k; break
            return "\n".join(lines[n:end]).strip() + "\n"
    for n, line in enumerate(lines):
        if case_id in line:
            end = len(lines)
            for k in range(n + 1, len(lines)):
                if any(o in lines[k] for o in others):
                    end = k; break
            return "\n".join(lines[n:end]).strip() + "\n"
    return None


def _val(line):
    return re.sub(r"^[\s\-*|]*[^:：]*?[:：]\s*", "", line).strip(" |")


def extract_window(section):
    """复现小节里的时间窗:① 复现开始 / 结束两行 → 「开始 ~ 结束」;② 带时间关键词的一行;③ 一行里有两个时间戳。找不到返回 ''。"""
    ts = re.compile(TS)
    start = end = None
    cand_key = cand_two = None
    for line in section.splitlines():
        if re.match(r"^#+\s", line) or not ts.search(line):
            continue
        if start is None and re.search(START_KEYS, line, re.I):
            start = _val(line); continue
        if end is None and re.search(END_KEYS, line, re.I):
            end = _val(line); continue
        if cand_key is None and re.search(WINDOW_KEYS, line, re.I):
            cand_key = re.sub(r"^[\s\-*|]*(?:" + WINDOW_KEYS + r")[^:：\d]{0,6}[:：]?\s*", "", line, flags=re.I).strip(" |")
        if cand_two is None and len(ts.findall(line)) >= 2:
            cand_two = line.strip(" |-*")
    if start and end:
        return f"{start} ~ {end}"
    if start:
        return start
    return cand_key or cand_two or ""


def extract_fix(*sections):
    """小节里的修复代码链接:先认 commit / PR / MR / diff 形态的 URL,再认带「修复 / PR / 补丁 / 链接」字样那行里的 URL。"""
    for sec in sections:
        if not sec:
            continue
        m = CODE_URL.search(sec)
        if m:
            return m.group(0).rstrip(")）,，。;；")
    for sec in sections:
        if not sec:
            continue
        for line in sec.splitlines():
            if re.search(FIX_KEYS, line, re.I):
                m = re.search(r"https?://\S+", line)
                if m:
                    return m.group(0).rstrip(")）,，。;；")
    return ""


def assemble(st, absp):
    """从复现文件(与可选的问题单文件)组装用例清单:[{id, window, fix, phenomenon, root_cause}]。"""
    ph_all = read(absp(st["phenomenon_file"])) if st.get("phenomenon_file") and os.path.isfile(absp(st["phenomenon_file"])) else ""
    rc_all = read(absp(st["root_cause_file"])) if st.get("root_cause_file") and os.path.isfile(absp(st["root_cause_file"])) else ""
    pattern = st.get("id_pattern") or ID_PATTERN_DEFAULT
    ids = discover_ids(ph_all, pattern)
    over = {}
    if st.get("tickets_file") and os.path.isfile(absp(st["tickets_file"])):
        tk = parse_tickets(read(absp(st["tickets_file"])))
        over = {t["id"]: t for t in tk}
        ids = [t["id"] for t in tk] if tk else ids   # 有问题单文件就以它为清单
    all_ids = sorted(set(ids) | set(discover_ids(rc_all, pattern)), key=len, reverse=True)
    cases = []
    for cid in ids:
        ph = extract_section(ph_all, cid, all_ids)
        rc = extract_section(rc_all, cid, all_ids)
        o = over.get(cid, {})
        cases.append({"id": cid, "phenomenon": ph, "root_cause": rc,
                      "window": o.get("window") or (extract_window(ph) if ph else ""),
                      "fix": o.get("fix") or extract_fix(rc, ph) or (st["ticket_template"].replace("{id}", cid) if st.get("ticket_template") else "")})
    return cases


def check(manifest_path):
    """自检:环境 + 文件 + 每个单号能否切到现象/根因、抓到窗/修复。返回(问题列表, 说明列表)。"""
    import shutil, urllib.request
    problems, notes = [], []
    if sys.version_info < (3, 8):
        problems.append(f"Python 需要 3.8+,当前 {sys.version.split()[0]}")
    cl = next((shutil.which(n) for n in ("claude", "claude.cmd", "claude.exe") if shutil.which(n)), None)
    (notes if cl else problems).append(f"claude CLI:{cl or '找不到,请安装 Claude Code 并加入 PATH'}")
    try:
        st = parse_manifest(read(manifest_path))["settings"]
    except Exception as e:
        return [f"batch.md 读不了:{e}"], notes
    base = os.path.dirname(os.path.abspath(manifest_path))
    absp = lambda p: os.path.abspath(os.path.join(base, p)) if p and not os.path.isabs(p) else p
    for k, zh in (("phenomenon_file", "复现文件"), ("root_cause_file", "根因文件")):
        if not st.get(k):
            problems.append(f"batch.md 缺「{zh}」")
        elif not os.path.isfile(absp(st[k])):
            problems.append(f"{zh}不存在:{absp(st[k])}")
    if not st.get("out"):
        problems.append("batch.md 缺「输出目录」")
    if st.get("source") and not os.path.isdir(absp(st["source"])):
        problems.append(f"源码树不存在:{absp(st['source'])}")
    elif not st.get("source"):
        notes.append("源码树:未配置,代码路径将全部 verified=false")
    if st.get("tickets_file") and not os.path.isfile(absp(st["tickets_file"])):
        problems.append(f"问题单文件不存在:{absp(st['tickets_file'])}")
    if st.get("mcp_config"):
        mp = absp(st["mcp_config"])
        if not os.path.isfile(mp):
            problems.append(f"MCP配置不存在:{mp}")
        else:
            try:
                cfg = json.load(open(mp, encoding="utf-8"))
                for name, srv in (cfg.get("mcpServers") or {}).items():
                    if not srv.get("url"):
                        continue
                    req = urllib.request.Request(srv["url"], data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "evidence-chain-check", "version": "0"}}}).encode(),
                                                 headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **(srv.get("headers") or {})}, method="POST")
                    try:
                        with urllib.request.urlopen(req, timeout=20) as r:
                            notes.append(f"MCP {name}:HTTP {r.status} 可达")
                    except Exception as e:
                        problems.append(f"MCP {name} 连不上:{srv['url']} ({e})")
            except ValueError as e:
                problems.append(f"MCP配置不是合法 JSON:{e}")
    else:
        notes.append("MCP配置:未指定,将继承 claude 配置目录里已配的 MCP(确认里面有 dbdog)")
    if problems:
        return problems, notes
    cases = assemble(st, absp)
    notes.append(f"复现文件里发现 {len(cases)} 个单号" + (f"(问题单文件限定)" if st.get("tickets_file") else ""))
    if not cases:
        problems.append("复现文件里没发现单号(标题行要含单号;格式特别可用「用例号正则」)")
    for c in cases:
        if not c["phenomenon"]:
            problems.append(f"{c['id']}:复现文件里切不到该单号的小节")
        if not c["root_cause"]:
            notes.append(f"{c['id']}:根因文件里没有,将跳过")
        notes.append(f"{c['id']}:事故窗 " + (f"「{c['window']}」" if c["window"] else "没抓到 ← 复现小节里要有复现开始/结束时间或带时间的行")
                     + " · 修复 " + (c["fix"] if c["fix"] else "无(按 fix_diff: absent)"))
        if c["fix"] and not re.match(r"^https?://", c["fix"]) and not os.path.isfile(absp(c["fix"])):
            problems.append(f"{c['id']}:修复 diff 文件不存在:{absp(c['fix'])}")
    return problems, notes


def summarize(out_dir, rows):
    status_zh = {"done": "完成", "dry_run": "只备好输入", "skipped_no_root_cause": "跳过:根因文件里没有该单号",
                 "skipped_no_phenomenon": "跳过:复现文件里没有该单号", "skipped_existing": "已有产物,跳过", "failed": "失败"}
    lines = ["# 反向取证批次汇总", "", f"- 生成时间:{time.strftime('%Y-%m-%d %H:%M:%S')}", f"- 单号数:{len(rows)}", "",
             "| 单号 | 状态 | 讲不讲得通 | 符合 | 不符 | 空/报错 | 无工具 | dbdog 侧发现 | 产物 |",
             "|---|---|---|---|---|---|---|---|---|"]
    js_rows = []
    for r in rows:
        cdir = os.path.join(out_dir, r["id"])
        js = os.path.join(cdir, "evidence-chain.json"); md = os.path.join(cdir, "evidence-chain.md")
        verdict = ""; cnt = {"obtained_match": 0, "obtained_mismatch": 0, "empty_or_error": 0, "no_tool": 0}; nf = 0
        if os.path.isfile(js):
            try:
                d = json.load(open(js, encoding="utf-8"))
                verdict = (d.get("consistency") or {}).get("verdict") or ""
                for e in d.get("evidence_chain") or []:
                    if e.get("outcome") in cnt:
                        cnt[e["outcome"]] += 1
                nf = len(d.get("dbdog_findings") or [])
            except ValueError:
                verdict = "json 不合法"
        has = os.path.isfile(md)
        lines.append(f"| {r['id']} | {status_zh.get(r['status'], r['status'])}{(':' + r['detail']) if r.get('detail') else ''} | {verdict} | "
                     + " | ".join(str(cnt[k]) if has else "" for k in ("obtained_match", "obtained_mismatch", "empty_or_error", "no_tool"))
                     + f" | {nf if has else ''} | {r['id'] + '/evidence-chain.md' if has else ''} |")
        js_rows.append({"id": r["id"], "status": r["status"], "detail": r.get("detail", ""), "verdict": verdict,
                        "outcomes": cnt, "dbdog_findings": nf, "evidence_chain_md": md if has else None})
    write(os.path.join(out_dir, "batch-summary.md"), "\n".join(lines) + "\n")
    write(os.path.join(out_dir, "batch-summary.json"), json.dumps({"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "cases": js_rows}, ensure_ascii=False, indent=2) + "\n")


def run_batch(manifest_path, dry_run=False, force=False, only=None):
    st = parse_manifest(read(manifest_path))["settings"]
    base = os.path.dirname(os.path.abspath(manifest_path))
    absp = lambda p: os.path.abspath(os.path.join(base, p)) if p and not os.path.isabs(p) else p
    out_dir = absp(st.get("out") or os.path.join(base, "out"))
    os.makedirs(out_dir, exist_ok=True)
    cases = assemble(st, absp)
    if only:
        cases = [c for c in cases if c["id"] in only]
    log(f"{len(cases)} 个单号:{', '.join(c['id'] for c in cases)}")
    sys.path.insert(0, HERE)
    import run as ec
    rows = []
    for i, c in enumerate(cases):
        cid = c["id"]; cdir = os.path.join(out_dir, cid); os.makedirs(cdir, exist_ok=True)
        row = {"id": cid, "status": "", "detail": ""}; rows.append(row)
        if not c["phenomenon"]:
            row["status"] = "skipped_no_phenomenon"; log(f"{cid}:复现文件里切不到,跳过"); continue
        if not c["root_cause"]:
            row["status"] = "skipped_no_root_cause"; log(f"{cid}:根因文件里没有,跳过"); continue
        if os.path.isfile(os.path.join(cdir, "evidence-chain.md")) and not force and not dry_run:
            row["status"] = "skipped_existing"; log(f"{cid}:已有产物,跳过(--force 重跑)"); continue
        write(os.path.join(cdir, "prompt.txt"), c["phenomenon"])
        write(os.path.join(cdir, "root-cause.md"), c["root_cause"])
        write(os.path.join(cdir, "window.txt"), c["window"] + "\n")
        log(f"{cid}:窗={c['window'] or '无'} · 修复={c['fix'] or '无'}")
        args = ["--phenomenon", os.path.join(cdir, "prompt.txt"), "--root-cause", os.path.join(cdir, "root-cause.md"),
                "--out", cdir, "--work", os.path.join(cdir, "work")]
        if c["window"]:
            args += ["--window", c["window"]]
        if st.get("source"):
            args += ["--source", absp(st["source"])]
        if c["fix"]:
            args += ["--fix", c["fix"] if re.match(r"^https?://", c["fix"]) else absp(c["fix"])]
        for k, flag in (("model", "--model"), ("config_dir", "--config-dir"), ("mcp_config", "--mcp-config")):
            if st.get(k):
                args += [flag, st[k] if k == "model" else absp(st[k])]
        if force:
            args.append("--force")
        if dry_run:
            write(os.path.join(cdir, "dry-run.txt"), "python run.py " + " ".join(args) + "\n")
            row["status"] = "dry_run"; continue
        try:
            ec.main(args); row["status"] = "done"
        except SystemExit as e:
            row["status"] = "failed"; row["detail"] = str(e)
        except Exception as e:   # 一个单号失败不拖累整批
            row["status"] = "failed"; row["detail"] = f"{type(e).__name__}: {e}"
        summarize(out_dir, rows)
        if i + 1 < len(cases) and st["interval_min"] > 0:
            log(f"歇 {st['interval_min']} 分钟再跑下一个({cases[i + 1]['id']})")
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
    ap.add_argument("--check", action="store_true", help="只自检:环境、文件、每个单号能否切到、窗/修复抓到什么;不跑")
    ap.add_argument("--dry-run", action="store_true", help="只切好每个单号的输入、写汇总,不起模型")
    ap.add_argument("--force", action="store_true", help="已有产物也重跑")
    ap.add_argument("--only", help="只跑这些单号,逗号分隔")
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
