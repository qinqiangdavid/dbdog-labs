#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""确定性预匹配(零模型):把反向证据链(evidence-chain.json)逐条对到正向假设图(forward-path.json)的工具调用上,
生成对比矩阵骨架 prematch.json,交给模型定最终结论。

  python prematch.py <单号目录>      # 目录里要有 evidence-chain.json 与 forward-path.json
"""
import json
import os
import re
import sys

LOCAL_TOOLS = {"Read", "Grep", "Glob", "Bash", "local_source_tree"}
PRELIM = {  # 反向取证结果 → 初判(与 agent 无关的三类直接定;另两类要看正向有没有调)
    "no_tool": "无工具",
    "empty_or_error": "应有结果但没有",
    "obtained_mismatch": "结果不对",
}


def tokens(s):
    """中英混合的粗分词:英文/数字词 + 中文双字。"""
    if not isinstance(s, str):
        return set()
    s = s.lower()
    out = set(re.findall(r"[a-z0-9_.:]{2,}", s))
    han = re.findall(r"[一-鿿]", s)
    out |= {a + b for a, b in zip(han, han[1:])}
    return out


def overlap(a, b):
    ta, tb = tokens(a), tokens(b)
    return len(ta & tb) / max(1, min(len(ta), len(tb))) if ta and tb else 0.0


def norm_tool(name):
    return (name or "").replace("mcp__dbdog__", "").replace("mcp__dbdog-mcp__", "").strip()


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def forward_calls(fp):
    """正向图里的全部工具调用(带假设归属)+ 未挂的。"""
    calls = []
    for n in fp.get("nodes") or []:
        for c in n.get("calls") or []:
            calls.append({**c, "hypothesis": n["id"], "hypothesis_text": n.get("text"), "tool": norm_tool(c.get("tool"))})
    for c in fp.get("unattached_tools") or []:
        calls.append({**c, "hypothesis": None, "hypothesis_text": None, "tool": norm_tool(c.get("tool"))})
    return sorted(calls, key=lambda c: c.get("seq") or 0)


def match(ec, fp):
    calls = forward_calls(fp)
    by_tool = {}
    for c in calls:
        by_tool.setdefault(c["tool"], []).append(c)
    rows = []
    for e in ec.get("evidence_chain") or []:
        tool = norm_tool(e.get("tool"))
        outcome = e.get("outcome")
        src = e.get("source")
        need = " ".join(str(e.get(k) or "") for k in ("name", "params", "expect_in_output"))
        if src == "local_source":
            cands = [c for c in calls if c["tool"] in LOCAL_TOOLS]
        else:
            cands = by_tool.get(tool, [])
        scored = []
        for c in cands:
            sc = overlap(need, " ".join(str(c.get(k) or "") for k in ("purpose", "intent")))
            scored.append((sc, c))
        scored.sort(key=lambda x: -x[0])
        hits = [{"seq": c.get("seq"), "hypothesis": c.get("hypothesis"), "agent": c.get("agent"), "status": c.get("status"),
                 "purpose": c.get("purpose"), "score": round(sc, 2)} for sc, c in scored[:5]]
        if outcome in PRELIM:
            prelim = PRELIM[outcome]
        elif not cands:
            prelim = "正向未调用(待判:假设没提到 / 工具没选对)"
        else:
            prelim = "正向已调用(待判:返回是否用上 / 推论是否做了)"
        rows.append({"id": e.get("id"), "name": e.get("name"), "tier": e.get("tier"), "source": src, "tool": tool or None,
                     "outcome": outcome, "reverse_actual": (e.get("actual") or "")[:300], "expect": (e.get("expect_in_output") or "")[:300],
                     "forward_hits": hits, "forward_same_tool_calls": len(cands), "prelim": prelim})
    # 正向调了、但反向证据链里没有任何一条用这个工具:agent 走的多余路
    rev_tools = {norm_tool(e.get("tool")) for e in ec.get("evidence_chain") or []} | LOCAL_TOOLS
    extra = {}
    for c in calls:
        if c["tool"] and c["tool"] not in rev_tools:
            extra.setdefault(c["tool"], {"tool": c["tool"], "count": 0, "hypotheses": set(), "seqs": []})
            extra[c["tool"]]["count"] += 1
            if c.get("hypothesis"):
                extra[c["tool"]]["hypotheses"].add(c["hypothesis"])
            extra[c["tool"]]["seqs"].append(c.get("seq"))
    for v in extra.values():
        v["hypotheses"] = sorted(v["hypotheses"]); v["seqs"] = v["seqs"][:10]
    hyps = [{"id": n["id"], "type": n.get("type"), "text": n.get("text"), "verdict": n.get("verdict"), "declared": n.get("declared"),
             "first_seq": n.get("first_seq"), "calls": len(n.get("calls") or [])} for n in fp.get("nodes") or []]
    counts = {}
    for r in rows:
        counts[r["prelim"]] = counts.get(r["prelim"], 0) + 1
    return {
        "case": ec.get("case"),
        "reverse": {"verdict": (ec.get("consistency") or {}).get("verdict"), "evidence": len(rows),
                    "dbdog_findings": ec.get("dbdog_findings") or [], "fix_diff": ec.get("fix_diff")},
        "forward": {"hypotheses": hyps, "tool_calls": len(calls), "unattached": len(fp.get("unattached_tools") or []),
                    "summary": fp.get("summary")},
        "matrix": rows,
        "forward_only_tools": sorted(extra.values(), key=lambda v: -v["count"]),
        "prelim_counts": counts,
    }


def run(case_dir):
    ecp, fpp = os.path.join(case_dir, "evidence-chain.json"), os.path.join(case_dir, "forward-path.json")
    for p in (ecp, fpp):
        if not os.path.isfile(p):
            raise SystemExit(f"缺 {p}")
    m = match(load(ecp), load(fpp))
    out = os.path.join(case_dir, "prematch.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2); f.write("\n")
    return out, m


if __name__ == "__main__":
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    out, m = run(sys.argv[1])
    print(f"prematch: 证据 {m['reverse']['evidence']} · 正向调用 {m['forward']['tool_calls']} · 初判 {m['prelim_counts']} → {out}", file=sys.stderr)
