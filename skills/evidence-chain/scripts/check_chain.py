#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验 evidence-chain.json:工具名必须是目录原名、source 三选一、outcome 四选一、缺口/空/不符的证据要进 dbdog_findings。
用法:python check_chain.py evidence-chain.json [--catalog dbdog-tool-catalog.json]
退出码 0 = 无问题(缺口不算问题,缺口是产出);1 = 有问题(逐条打印)。"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(os.path.dirname(HERE), "references", "dbdog-tool-catalog.json")
SOURCES = ("dbdog", "local_source", "unavailable")
OUTCOMES = ("obtained_match", "obtained_mismatch", "empty_or_error", "no_tool")
FINDING_KINDS = {"unavailable": "no_tool", "no_tool": "no_tool", "empty_or_error": "empty_or_error", "obtained_mismatch": "obtained_mismatch"}


def load_catalog(path=CATALOG):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    tools = d.get("tools", d) if isinstance(d, dict) else d
    return {t["name"] for t in tools if isinstance(t, dict) and t.get("name")}


def check(chain, catalog):
    problems, counts, gaps = [], {s: 0 for s in SOURCES}, []
    outcomes = {o: 0 for o in OUTCOMES}
    finding_ids = {g.get("evidence_id") for g in (chain.get("dbdog_findings") or chain.get("tool_gaps") or [])}
    for e in chain.get("evidence_chain") or []:
        eid = e.get("id") or "?"
        src = e.get("source")
        if src not in SOURCES:
            problems.append(f"{eid}: source 缺失或非法({src!r}),应为 dbdog/local_source/unavailable")
            continue
        counts[src] += 1
        tool = e.get("tool")
        oc = e.get("outcome")
        if oc not in OUTCOMES:
            problems.append(f"{eid}: outcome 缺失或非法({oc!r}),应为 " + "/".join(OUTCOMES))
        else:
            outcomes[oc] += 1
            if src == "unavailable" and oc != "no_tool":
                problems.append(f"{eid}: source=unavailable 时 outcome 应为 no_tool,实际 {oc!r}")
            if src != "unavailable" and oc == "no_tool":
                problems.append(f"{eid}: outcome=no_tool 但 source={src},矛盾")
            if oc in ("empty_or_error", "obtained_mismatch") and eid not in finding_ids:
                problems.append(f"{eid}: outcome={oc} 却没有进 dbdog_findings")
        if src == "dbdog":
            if tool not in catalog:
                problems.append(f"{eid}: source=dbdog 但 tool={tool!r} 不是目录里的工具原名")
        elif src == "local_source":
            if tool != "local_source_tree":
                problems.append(f"{eid}: source=local_source 时 tool 应为 local_source_tree,实际 {tool!r}")
        else:
            gaps.append(eid)
            if tool:
                problems.append(f"{eid}: source=unavailable 时 tool 应为 null,实际 {tool!r}")
            for k in ("needed_capability", "closest_tool", "why_insufficient"):
                if not e.get(k):
                    problems.append(f"{eid}: source=unavailable 但缺 {k}")
            if eid not in finding_ids:
                problems.append(f"{eid}: source=unavailable 却没有进 dbdog_findings")
    return {"problems": problems, "counts": counts, "gaps": gaps, "outcomes": outcomes}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("chain")
    ap.add_argument("--catalog", default=CATALOG)
    a = ap.parse_args(argv)
    with open(a.chain, encoding="utf-8") as f:
        chain = json.load(f)
    r = check(chain, load_catalog(a.catalog))
    c = r["counts"]
    o = r["outcomes"]
    print(f"证据 {sum(c.values())} 条:dbdog {c['dbdog']} · 源码 {c['local_source']} · 取不到 {c['unavailable']}"
          + (f"(缺口:{', '.join(r['gaps'])})" if r["gaps"] else ""))
    print(f"取证结果:符合 {o['obtained_match']} · 不符 {o['obtained_mismatch']} · 空/报错 {o['empty_or_error']} · 无工具 {o['no_tool']}")
    for p in r["problems"]:
        print("✗ " + p)
    return 1 if r["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
