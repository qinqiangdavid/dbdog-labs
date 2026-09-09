#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 dbdog-mcp 拉工具目录快照 → references/dbdog-tool-catalog.json(反向角只在这份目录里选工具)。
用法:python3 fetch-catalog.py --url 'http://host:port/mcp?toolsets=...' (--bearer-file ~/.datadog/dbdog-mcp-bearer.txt | --api-key KEY)
不带参数时只提示;目录里已有的 local_source_tree 条目(源码树证据面)会保留。"""
import argparse, json, os, sys, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "references", "dbdog-tool-catalog.json")
ap = argparse.ArgumentParser(); ap.add_argument("--url", required=True); ap.add_argument("--bearer-file"); ap.add_argument("--api-key")
a = ap.parse_args()
H = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
if a.bearer_file: H["Authorization"] = "Bearer " + open(os.path.expanduser(a.bearer_file)).read().strip()
if a.api_key: H["DD-API-KEY"] = a.api_key
def call(body, sid=None):
    h = dict(H); h.update({"Mcp-Session-Id": sid} if sid else {})
    r = urllib.request.urlopen(urllib.request.Request(a.url, data=json.dumps(body).encode(), headers=h, method="POST"), timeout=30)
    sid = r.headers.get("Mcp-Session-Id") or sid; t = r.read().decode()
    if not t.strip(): return None, sid
    for line in t.splitlines():
        if line.startswith("data:"): t = line[5:].strip()
    return json.loads(t), sid
_, sid = call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "diag-pair", "version": "0"}}})
call({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
tools, _ = call({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, sid)
cat = [{"name": t["name"], "description": (t.get("description") or "")[:700], "params": {k: (v.get("description") or "")[:120] for k, v in ((t.get("inputSchema") or {}).get("properties", {})).items()}} for t in tools["result"]["tools"]]
keep = []
if os.path.exists(OUT):
    keep = [t for t in json.load(open(OUT, encoding="utf-8")).get("tools", []) if t["name"] == "local_source_tree"]
json.dump({"source": a.url.split("?")[0], "tools": keep + cat}, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"{len(cat)} tools (+{len(keep)} 保留的证据面) → {OUT}")
