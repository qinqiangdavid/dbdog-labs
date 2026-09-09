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
  | 用例 | 事故窗 | 修复 | 备注 |      修复列:DTS 单地址 / PR 地址 / 本地 diff,可空
可选列「现象文件」「根因文件」按行覆盖全局文件(直接给该用例的文件路径)。
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
}
COLS = {"用例": "id", "事故窗": "window", "修复": "fix", "备注": "note", "现象文件": "phenomenon_file", "根因文件": "root_cause_file",
        "span文件": "spans", "span 文件": "spans", "spans": "spans"}
SPAN_GRAPH = os.path.join(os.path.dirname(os.path.dirname(HERE)), "span-graph", "scripts", "from_spans.py")


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
    if not cases:
        problems.append("batch.md 里没有用例表(| 用例 | 事故窗 | 修复 | 备注 |)")
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
    ph_all = read(absp(st["phenomenon_file"])) if st.get("phenomenon_file") and os.path.isfile(absp(st["phenomenon_file"])) else ""
    rc_all = read(absp(st["root_cause_file"])) if st.get("root_cause_file") and os.path.isfile(absp(st["root_cause_file"])) else ""
    ids = [c["id"] for c in cases]
    for c in cases:
        cid = c["id"]
        if not (c.get("phenomenon_file") or extract_section(ph_all, cid, ids)):
            problems.append(f"{cid}:现象文件里切不到该用例")
        if not (c.get("root_cause_file") or extract_section(rc_all, cid, ids)):
            notes.append(f"{cid}:根因文件里没有,将跳过")
        if not c.get("window"):
            notes.append(f"{cid}:没填事故窗,推导角只能按题面里的时间取证")
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
        if os.path.isfile(os.path.join(cdir, "evidence-chain.md")) and not force and not dry_run:
            row["status"] = "skipped_existing"; log(f"{cid}:已有产物,跳过(--force 重跑)"); continue
        ph = read(absp(c["phenomenon_file"])) if c.get("phenomenon_file") else extract_section(ph_all, cid, ids)
        if not ph:
            row["status"] = "skipped_no_phenomenon"; log(f"{cid}:现象文件里没找到该用例,跳过"); continue
        rc = read(absp(c["root_cause_file"])) if c.get("root_cause_file") else extract_section(rc_all, cid, ids)
        if not rc:
            row["status"] = "skipped_no_root_cause"; log(f"{cid}:根因文件里没找到该用例,跳过"); continue
        write(os.path.join(cdir, "prompt.txt"), ph)
        write(os.path.join(cdir, "root-cause.md"), rc)
        write(os.path.join(cdir, "window.txt"), (c.get("window") or "").strip() + "\n")
        if c.get("spans"):
            sp = absp(c["spans"])
            if os.path.exists(sp) and os.path.isfile(SPAN_GRAPH):
                import subprocess
                r = subprocess.run([sys.executable, SPAN_GRAPH, sp, "--out", cdir], capture_output=True, text=True)
                log(f"{cid}:正向图 " + ("✓" if r.returncode == 0 else "✗ " + r.stderr.strip()[-200:]))
            else:
                log(f"{cid}:⚠ span 文件或 span-graph skill 不在({sp}),跳过正向图")
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
        for k, flag in (("model", "--model"), ("config_dir", "--config-dir"), ("mcp_config", "--mcp-config")):
            if st.get(k):
                args += [flag, absp(st[k]) if k != "model" else st[k]]
        if force:
            args.append("--force")
        log(f"{cid}:修复来源={kind} · 窗={c.get('window') or '无'}")
        if dry_run:
            write(os.path.join(cdir, "dry-run.txt"), "python run.py " + " ".join(args) + "\n")
            row["status"] = "dry_run"; continue
        try:
            ec.main(args)
            row["status"] = "done"
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
