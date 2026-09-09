#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""diag-compare 批次:扫输出父目录,每个同时有 evidence-chain.json 与 forward-path.json 的单号目录出 compare.md,
再跨单号聚合成 improvements.md(同一条 dbdog 缺口在几个单号里撞到就排前面)。

  python batch.py <输出父目录> [--config-dir DIR] [--model NAME] [--force] [--only ID1,ID2] [--aggregate-only]
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CATS = ("采集面", "工具契约", "数据正确性", "skill方法论")


def log(msg):
    print(f"[compare-batch] {msg}", file=sys.stderr)


def ready_cases(out_dir):
    cases = []
    for name in sorted(os.listdir(out_dir)):
        d = os.path.join(out_dir, name)
        if os.path.isdir(d) and os.path.isfile(os.path.join(d, "evidence-chain.json")) and os.path.isfile(os.path.join(d, "forward-path.json")):
            cases.append(name)
    return cases


def norm_title(t):
    t = re.sub(r"\s+", "", str(t or "")).lower()
    t = re.sub(r"[（(].*?[)）]", "", t)
    return t


def aggregate(out_dir, cases):
    """跨单号聚合 compare.json 的 improvements → improvements.md / .json。"""
    groups = {}
    per_case = []
    for cid in cases:
        p = os.path.join(out_dir, cid, "compare.json")
        if not os.path.isfile(p):
            continue
        try:
            d = json.load(open(p, encoding="utf-8"))
        except ValueError:
            continue
        per_case.append({"id": cid, "counts": d.get("counts") or {}, "main_layer": d.get("main_layer"),
                         "reverse_verdict": d.get("reverse_verdict"), "pinned": d.get("root_cause_pinned_by_agent"),
                         "first_wrong_step": (d.get("first_wrong_step") or {}).get("seq")})
        for imp in d.get("improvements") or []:
            cat = imp.get("category") if imp.get("category") in CATS else "skill方法论"
            key = (cat, norm_title(imp.get("title")) or norm_title(imp.get("tool")))
            g = groups.setdefault(key, {"category": cat, "title": imp.get("title"), "tool": imp.get("tool"), "cases": [], "fixes": [], "impacts": {}, "evidence": []})
            if cid not in g["cases"]:
                g["cases"].append(cid)
            if imp.get("fix") and imp["fix"] not in g["fixes"]:
                g["fixes"].append(imp["fix"])
            g["impacts"][imp.get("impact") or "?"] = g["impacts"].get(imp.get("impact") or "?", 0) + 1
            g["evidence"].append(f"{cid}:{','.join(imp.get('evidence') or [])}")
    ranked = sorted(groups.values(), key=lambda g: (-len(g["cases"]), CATS.index(g["category"]) if g["category"] in CATS else 9))
    lines = ["# dbdog 改进清单(跨单号聚合)", "", f"- 生成时间:{time.strftime('%Y-%m-%d %H:%M:%S')} · 单号 {len(per_case)} 个 · 条目 {len(ranked)} 条", "",
             "## 按命中单号数排序", "", "| # | 类别 | 标题 | 工具 | 命中单号 | 影响 | 建议改法 |", "|---|---|---|---|---|---|---|"]
    for i, g in enumerate(ranked, 1):
        imp = ", ".join(f"{k}×{v}" for k, v in sorted(g["impacts"].items(), key=lambda kv: -kv[1]))
        fix = (g["fixes"][0] if g["fixes"] else "").replace("|", "\\|")
        lines.append(f"| {i} | {g['category']} | {str(g['title']).replace('|', '/')} | {g.get('tool') or ''} | {len(g['cases'])}:{','.join(g['cases'])} | {imp} | {fix} |")
    lines += ["", "## 各单号总判", "", "| 单号 | agent 定住根因 | 反向 verdict | 主要在哪层 | 第一步走错 seq | 无工具 | 应有结果但没有 | 结果不对 | 假设没提到 | 工具没调或调错 | 调对了但推理错 |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in per_case:
        k = c["counts"]
        lines.append(f"| {c['id']} | {'是' if c['pinned'] else '否'} | {c['reverse_verdict'] or ''} | {c['main_layer'] or ''} | {c['first_wrong_step'] or ''} | "
                     + " | ".join(str(k.get(x, 0)) for x in ("无工具", "应有结果但没有", "结果不对", "假设没提到", "工具没调或调错", "调对了但推理错")) + " |")
    lines += ["", "## 按类别", ""]
    for cat in CATS:
        items = [g for g in ranked if g["category"] == cat]
        lines.append(f"### {cat}({len(items)} 条)")
        lines.append("")
        for g in items:
            lines.append(f"- **{g['title']}**" + (f"(`{g['tool']}`)" if g.get("tool") else "") + f" —— 命中 {len(g['cases'])} 个单号:{', '.join(g['cases'])}")
            for fx in g["fixes"][:2]:
                lines.append(f"  - 改法:{fx}")
            lines.append(f"  - 依据:{'; '.join(g['evidence'][:4])}")
        lines.append("")
    md = os.path.join(out_dir, "improvements.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(os.path.join(out_dir, "improvements.json"), "w", encoding="utf-8") as f:
        json.dump({"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "cases": per_case, "improvements": ranked}, f, ensure_ascii=False, indent=2); f.write("\n")
    return md, ranked, per_case


def main(argv=None):
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="batch.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_dir", help="输出父目录(下面按单号建的子目录)")
    ap.add_argument("--config-dir"); ap.add_argument("--model")
    ap.add_argument("--force", action="store_true"); ap.add_argument("--only")
    ap.add_argument("--aggregate-only", action="store_true", help="不跑模型,只重新聚合已有的 compare.json")
    a = ap.parse_args(argv)
    out_dir = os.path.abspath(a.out_dir)
    cases = ready_cases(out_dir)
    if a.only:
        only = set(a.only.split(","))
        cases = [c for c in cases if c in only]
    log(f"两份产物齐全的单号 {len(cases)} 个:{', '.join(cases) or '无'}")
    failed = 0
    if not a.aggregate_only:
        sys.path.insert(0, HERE)
        import run as cr
        for cid in cases:
            args = [os.path.join(out_dir, cid)] + (["--config-dir", a.config_dir] if a.config_dir else []) + (["--model", a.model] if a.model else []) + (["--force"] if a.force else [])
            try:
                cr.main(args)
            except SystemExit as e:
                failed += 1; log(f"{cid}:失败 {e}")
            except Exception as e:
                failed += 1; log(f"{cid}:失败 {type(e).__name__}: {e}")
    md, ranked, per = aggregate(out_dir, cases)
    log(f"聚合 → {md}(单号 {len(per)} · 改进 {len(ranked)} 条)")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
