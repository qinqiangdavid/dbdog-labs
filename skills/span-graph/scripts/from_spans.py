#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook span → 正向假设图(假设↔假设、假设↔工具、假设收口)。零模型,只用标准库,Windows/macOS/Linux 通用。

输入:spans.jsonl(每行一个 span)/ server 导出 {"spans":[...]} / 含 spans.jsonl 的目录。
解析与 hook hypothesis.mjs、loop/lib/build-hypotheses.py 对齐:tags 优先,否则解析 intent 的 [H2<H1] 头。
正文「提出」事件(2026-09-08):hook 只采原文不做语义解析,「提出 [H2] 类型=…; 假设=…」写在正文里的
父节点从 llm/agent span 的正文提——本地 spans.jsonl 全量字段 output_local / thinking_local 优先
(读侧口径 x_local ?? x),server 导出只有截断后的 output。正则与跳过规则同 build-hypotheses.py。
"""
import argparse
import json
import os
import re
import sys

ID = r"H[0-9]+(?:\.[0-9]+)*"
HEAD = re.compile(rf"^\s*\[\s*({ID})\s*(?:<\s*({ID})\s*>?)?\s*\]\s*(.*)$", re.S)   # 父编号后可带多余的 >(模板被照抄成 [H2.1<H2>])
KV = re.compile(r"^\s*(假设|判据|关|意图|类型)\s*=\s*(.*?)\s*$")
RES = re.compile(rf"^\s*({ID})\s*:\s*(证伪|证实|未决)\s*$")
TYPE = {"现象确认": "confirm", "根因": "cause", "前提": "confirm"}
VERDICT = {"证伪": "falsified", "证实": "confirmed", "未决": "open"}
TYPE_ZH = {"confirm": "现象确认", "cause": "根因"}
VERDICT_ZH = {"falsified": "证伪", "confirmed": "证实", "open": "未决"}
PROPOSE = re.compile(rf"提出\s*\[\s*({ID})")                              # 正文里显式提出
CLOSING_HEAD = re.compile(r"^\s*#{0,6}\s*\**\s*假设收口")                      # 结论末尾的「## 假设收口」小节
CLOSING_LINE = re.compile(rf"^\s*[-*|]?\s*\**\s*({ID})\s*\**\s*[:：]?\s*(证伪|证实|未决)")
PROTOCOL_RESTATE = re.compile(r"书写约定|telemetry\.intent|格式固定")       # 复述约定的行不算提出
RESTATE_LINE = re.compile(r"示例|H<编号|假设=<|判据=<|\[H\d[^\]]*\]\s*假设=")   # 模板/示例行(占位符或示例编号)


def normalize(s):
    return (s.replace("＜", "<").replace("＝", "=").replace("；", ";")
             .replace("：", ":").replace("，", ",").replace("　", " "))


def parse_fields(body):
    out = {}
    for seg in re.split(r"[;\n]", body):
        kv = KV.match(seg)
        if not kv or not kv.group(2):
            continue
        k, v = kv.group(1), kv.group(2)
        if k == "假设":
            out["text"] = v
        elif k == "判据":
            out["expect"] = v
        elif k == "意图":
            out["purpose"] = v
        elif k == "类型" and v in TYPE:
            out["type"] = TYPE[v]
        elif k == "关":
            rs = []
            for part in v.split(","):
                r = RES.match(part)
                if r:
                    rs.append({"id": r.group(1), "verdict": VERDICT[r.group(2)]})
            if rs:
                out["resolve"] = rs
    return out


def parse_intent(intent):
    """带 [H..] 头的 intent → dict;没头 → None。"""
    if not isinstance(intent, str) or not intent.strip():
        return None
    m = HEAD.match(normalize(intent))
    if not m:
        return None
    out = {"id": m.group(1), "parent": m.group(2)}
    out.update(parse_fields(m.group(3)))
    return out


def has_fields_without_head(intent):
    """写了 假设=/判据= 等字段却没有 [H..] 头(被测 agent 不守约定的典型形态)。"""
    if not isinstance(intent, str) or not intent.strip():
        return False
    return bool(parse_fields(normalize(intent)))


def type_from_tag(raw):
    if raw in ("confirm", "cause"):
        return raw
    return TYPE.get(raw)


def span_intent(s):
    return s.get("intent") or (s.get("tags") or {}).get("intent")


def parsed_from_span(s):
    tags = s.get("tags") or {}
    hid = (tags.get("hypothesis_id") or "").strip()
    if hid:
        parent = (tags.get("parent_hypothesis_id") or "").strip() or None
        out = {"id": hid, "parent": parent}
        t = type_from_tag(tags.get("hypothesis_type"))
        if t:
            out["type"] = t
        if tags.get("hypothesis"):
            out["text"] = tags["hypothesis"]
        if tags.get("expect"):
            out["expect"] = tags["expect"]
        if tags.get("resolve"):
            try:
                rs = json.loads(tags["resolve"])
                if isinstance(rs, list) and rs:
                    out["resolve"] = rs
            except ValueError:
                pass
        # 意图 只在 intent 原文里,tags 不带
        p = parse_intent(span_intent(s))
        if p and p.get("purpose"):
            out["purpose"] = p["purpose"]
        return out
    return parse_intent(span_intent(s))


def prose_fields(s):
    """llm/agent span 可扫的正文:(来源名, 文本)。本地全量字段优先,server 导出退回截断值。"""
    out = []
    body = s.get("output_local") if isinstance(s.get("output_local"), str) else s.get("output")
    if isinstance(body, str) and body.strip():
        out.append(("output", body))
    th = s.get("thinking_local")
    if isinstance(th, str) and th.strip():
        out.append(("thinking", th))
    return out


def scan_closing(text):
    """结论正文「## 假设收口」小节里的「H1 证伪 —— 依据」→ [(hid, verdict)]。只认该小节之内、到下一个标题为止。"""
    if not isinstance(text, str) or not text.strip():
        return []
    out, inside = [], False
    for line in normalize(text).splitlines():
        if CLOSING_HEAD.match(line):
            inside = True
            continue
        if inside and re.match(r"^\s*#{1,6}\s", line):
            break
        if inside:
            m = CLOSING_LINE.match(line)
            if m:
                out.append((m.group(1), VERDICT[m.group(2)]))
    return out


def scan_proposals(text):
    """正文里的「提出 [H2] 类型=…; 假设=…」→ [(hid, parsed)],按出现顺序。
    复述约定的行、模板/示例行整行跳过(否则示例编号会被当成提出)。"""
    if not isinstance(text, str) or not text.strip():
        return []
    norm = normalize(text)
    kept = [l for l in norm.split("\n") if not (PROTOCOL_RESTATE.search(l) or RESTATE_LINE.search(l))]
    norm = "\n".join(kept)
    found = []
    for m in PROPOSE.finditer(norm):
        line_end = norm.find("\n", m.end())
        body = norm[m.start() + 2:line_end if line_end != -1 else len(norm)].strip()   # 去掉「提出」二字
        p = parse_intent(body)
        if p:
            found.append((m.group(1), p))
    return found


def read_jsonl(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def resolve_input(path):
    if os.path.isdir(path):
        cand = os.path.join(path, "spans.jsonl")
        if os.path.isfile(cand):
            return cand
        jsonls = sorted(p for p in os.listdir(path) if p.endswith(".jsonl"))
        if len(jsonls) == 1:
            return os.path.join(path, jsonls[0])
        raise SystemExit(f"目录里没有 spans.jsonl:{path}")
    if not os.path.isfile(path):
        raise SystemExit(f"找不到:{path}")
    return path


def load_spans(path, session=None, trace=None):
    with open(path, encoding="utf-8", errors="replace") as f:
        head = f.read(4096)
    if head.lstrip().startswith("{") and '"spans"' in head[:2000] and "\n{" not in head:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        spans = d.get("spans") or d.get("data") or []
    elif head.lstrip().startswith("["):
        with open(path, encoding="utf-8") as f:
            spans = json.load(f)
    else:
        spans = list(read_jsonl(path))
    last = {}
    for s in spans:
        if not isinstance(s, dict) or "span_id" not in s:
            continue
        if session and s.get("session_id") != session:
            continue
        if trace and s.get("trace_id") != trace:
            continue
        last[s.get("span_id")] = s   # 同 span_id 后写赢(hook 重发 root/agent span)
    return list(last.values())


def ensure(nodes, hid):
    if hid not in nodes:
        nodes[hid] = {"id": hid, "parent": None, "type": None, "text": None, "expect": None,
                      "verdict": "open", "declared": False, "first_seq": None, "closed_by": None,
                      "proposed_in": None, "calls": []}
    return nodes[hid]


def fill(n, p):
    if p.get("parent") and not n["parent"]:
        n["parent"] = p["parent"]
    if p.get("type") and not n["type"]:
        n["type"] = p["type"]
    if p.get("text") and not n["text"]:
        n["text"] = p["text"]
    if p.get("expect") and not n["expect"]:
        n["expect"] = p["expect"]


def sort_key(s):
    ts = s.get("ts") or s.get("ts_ms") or 0
    return (str(ts) if not isinstance(ts, (int, float)) else f"{ts:020.3f}", s.get("span_id") or "")


def agent_label(s):
    aid = (s.get("tags") or {}).get("agent_id")
    return aid[:8] if aid else "main"


def build(spans):
    nodes = {}
    tool_edges = []
    resolve_edges = []
    unattached = []
    traces = set()
    ordered = sorted(spans, key=sort_key)
    seq = 0
    for s in ordered:
        if s.get("trace_id"):
            traces.add(s["trace_id"])
        p = parsed_from_span(s)
        if s.get("kind") != "tool":
            if p:
                fill(ensure(nodes, p["id"]), p)
            # 正文「提出」事件:最早一次为准(span 已按 ts 排序;同 span 内 output 先于 thinking 无所谓,
            # 同一 hid 只记第一次)
            for source, text in prose_fields(s):
                for hid, pp in scan_proposals(text):
                    n = ensure(nodes, hid)
                    if n["proposed_in"] is None:
                        n["proposed_in"] = {"span_id": s.get("span_id"), "in": source}
                        fill(n, pp)
                # 正文收口:结论末尾「## 假设收口」小节;工具调用上的 关= 优先,正文只补没关过的
                for hid, verdict in scan_closing(text):
                    n = ensure(nodes, hid)
                    if n["verdict"] == "open" or (n.get("closed_by") or {}).get("in") == "prose":
                        n["verdict"] = verdict
                        n["closed_by"] = {"from": "正文收口", "in": "prose", "span_id": s.get("span_id")}
                        # 同一段结论会同时出现在 root span 与末轮 llm span 的 output 里,只记一条
                        if not any(e["from"] == "正文收口" and e["to"] == hid and e["verdict"] == verdict for e in resolve_edges):
                            resolve_edges.append({"kind": "resolve", "from": "正文收口", "to": hid, "verdict": verdict, "span_id": s.get("span_id")})
            continue
        seq += 1
        tool = (s.get("name") or "").replace("mcp__dbdog__", "")
        intent = span_intent(s)
        call = {"seq": seq, "span_id": s.get("span_id"), "tool": tool, "ts": s.get("ts"),
                "agent": agent_label(s), "status": s.get("status"), "intent": intent,
                "purpose": (p or {}).get("purpose")}
        if not p:
            call["reason"] = "intent_without_head" if has_fields_without_head(intent) else "no_intent"
            unattached.append(call)
            continue
        n = ensure(nodes, p["id"])
        n["declared"] = True
        if n["first_seq"] is None:
            n["first_seq"] = seq
        fill(n, p)
        for r in p.get("resolve") or []:
            c = ensure(nodes, r["id"])
            c["verdict"] = r["verdict"]
            c["closed_by"] = {"from": p["id"], "seq": seq, "span_id": s.get("span_id")}
            resolve_edges.append({"kind": "resolve", "from": p["id"], "to": r["id"],
                                  "verdict": r["verdict"], "span_id": s.get("span_id")})
        n["calls"].append(call)
        tool_edges.append({"kind": "tool", "from": p["id"], "tool": tool, "seq": seq,
                           "span_id": s.get("span_id"), "intent": intent})

    parent_edges = []
    seen = set()
    for n in list(nodes.values()):
        if n["parent"]:
            ensure(nodes, n["parent"])
            key = (n["parent"], n["id"])
            if key not in seen:
                seen.add(key)
                parent_edges.append({"kind": "parent", "from": n["parent"], "to": n["id"]})

    def hid_key(h):
        return [int(x) if x.isdigit() else x for x in h.replace("H", "").split(".")]

    node_list = sorted(nodes.values(), key=lambda n: hid_key(n["id"]))
    return {
        "trace_ids": sorted(traces),
        "span_count": len(spans),
        "tool_call_count": seq,
        "nodes": node_list,
        "edges": parent_edges + tool_edges + resolve_edges,
        "unattached_tools": unattached,
        "summary": {
            "hypotheses": len(node_list),
            "undeclared": sum(1 for n in node_list if not n["declared"]),
            "parent_edges": len(parent_edges),
            "tool_edges": len(tool_edges),
            "resolve_edges": len(resolve_edges),
            "unattached_tools": len(unattached),
            "unattached_intent_without_head": sum(1 for u in unattached if u["reason"] == "intent_without_head"),
            "proposed_in_prose": sum(1 for n in node_list if n.get("proposed_in")),
        },
    }


def short_ts(ts):
    if isinstance(ts, str) and "T" in ts:
        return ts[11:19]
    return "" if ts is None else str(ts)


def render_md(g):
    s = g["summary"]
    lines = [
        "# 正向假设图(从 hook span 重构)",
        "",
        f"- trace:`{', '.join(g['trace_ids']) or '—'}`",
        f"- span {g['span_count']} 条,其中工具调用 {g['tool_call_count']} 次",
        f"- 假设 {s['hypotheses']} 个(其中 {s['undeclared']} 个只被引用、未在调用上声明)· "
        f"假设↔假设边 {s['parent_edges']} · 假设↔工具边 {s['tool_edges']} · 收口边 {s['resolve_edges']} · "
        f"未挂到假设的工具调用 {s['unattached_tools']}(其中 {s['unattached_intent_without_head']} 次写了字段但 intent 不带 [H..] 头)· "
        f"正文提出 {s.get('proposed_in_prose', 0)}",
        "",
        "读法:节点 = 假设;缩进 = `[H2.1<H2]` 声明的父子关系;每个假设下面的表 = 该假设名下的工具调用(seq 是整条 trace 的全局序号,可据此看先后)。",
        "",
        "## 假设树(假设↔假设、假设↔工具)",
        "",
    ]
    kids = {}
    for e in g["edges"]:
        if e["kind"] == "parent":
            kids.setdefault(e["from"], []).append(e["to"])
    by_id = {n["id"]: n for n in g["nodes"]}
    roots = [n for n in g["nodes"] if not n.get("parent") or n["parent"] not in by_id]

    def node_line(n, depth):
        nonlocal lines
        typ = TYPE_ZH.get(n.get("type"), "类型未写")
        ver = VERDICT_ZH.get(n["verdict"], n["verdict"])
        head = "#" * min(3 + depth, 6)
        title = f"{head} {n['id']} · {typ} · {ver}"
        if n.get("closed_by"):
            cb = n["closed_by"]
            title += f"(由 {cb['from']} 在 seq {cb['seq']} 关闭)" if cb.get("seq") else "(结论正文「假设收口」里关闭,工具调用上没写 关=)"
        lines.append(title)
        lines.append("")
        if n.get("proposed_in"):
            src = "思考块" if n["proposed_in"]["in"] == "thinking" else "正文"
            lines.append(f"- 提出于{src}(span `{n['proposed_in']['span_id']}`):{n.get('text') or '(未写 假设=)'}")
            if n.get("expect"):
                lines.append(f"- 判据:{n['expect']}")
            if not n["declared"]:
                lines.append(f"- 未声明:没有任何工具调用以 `[{n['id']}]` 开头(只在正文提出、由子假设取证)。")
            else:
                lines.append(f"- 首次出现:seq {n['first_seq']}")
        elif not n["declared"]:
            lines.append(f"- 未声明:没有任何工具调用以 `[{n['id']}]` 开头,只在子假设或收口里被引用,"
                         "正文里也没有「提出 [H..]」行(server 导出的 output 截断过,本地 spans.jsonl 才是全量)。")
        else:
            lines.append(f"- 假设:{n.get('text') or '(未写 假设=)'}")
            lines.append(f"- 判据:{n.get('expect') or '(未写 判据=)'}")
            lines.append(f"- 首次出现:seq {n['first_seq']}")
        if n["calls"]:
            lines += ["", "| seq | 时间 | 代理 | 工具 | 意图 | 状态 |", "|---|---|---|---|---|---|"]
            for c in n["calls"]:
                purpose = (c.get("purpose") or "").replace("|", "\\|")
                lines.append(f"| {c['seq']} | {short_ts(c.get('ts'))} | {c['agent']} | `{c['tool']}` | {purpose} | {c.get('status') or ''} |")
        lines.append("")

    def walk(hid, depth=0):
        node_line(by_id[hid], depth)
        for k in kids.get(hid, []):
            walk(k, depth + 1)

    for r in roots:
        walk(r["id"])

    lines += ["## 假设出现顺序", ""]
    order = sorted((n for n in g["nodes"] if n["first_seq"] is not None), key=lambda n: n["first_seq"])
    for n in order:
        lines.append(f"- seq {n['first_seq']}:{n['id']}({TYPE_ZH.get(n.get('type'), '类型未写')})"
                     + (f" ← 父 {n['parent']}" if n.get("parent") else ""))
    lines.append("")

    lines += ["## 假设收口(关=)", ""]
    res = [e for e in g["edges"] if e["kind"] == "resolve"]
    if res:
        for e in res:
            lines.append(f"- {e['from']} → {e['to']}:{VERDICT_ZH.get(e['verdict'], e['verdict'])}(span `{e['span_id']}`)")
    else:
        lines.append("- 没有任何调用写 关=,全部假设停在未决。")
    lines.append("")

    lines += ["## 未挂到假设的工具调用", ""]
    bad = [u for u in g["unattached_tools"] if u["reason"] == "intent_without_head"]
    plain = [u for u in g["unattached_tools"] if u["reason"] != "intent_without_head"]
    if bad:
        lines += [f"### 写了字段但 intent 不带 [H..] 头({len(bad)} 次,被测 agent 未守约定)", ""]
        for u in bad:
            it = (u.get("intent") or "").replace("\n", " ").replace("|", "\\|")
            lines.append(f"- seq {u['seq']} `{u['tool']}`({u['agent']}):{it[:160]}")
        lines.append("")
    if plain:
        counts = {}
        for u in plain:
            counts[u["tool"]] = counts.get(u["tool"], 0) + 1
        lines += [f"### 不带 intent({len(plain)} 次:本地工具与子代理派发等)", ""]
        for t, c in sorted(counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"- `{t}` × {c}")
        lines.append("")
    if not g["unattached_tools"]:
        lines.append("- 无")
        lines.append("")
    return "\n".join(lines)


def run(path, out=None, session=None, trace=None):
    src = resolve_input(path)
    spans = load_spans(src, session, trace)
    if not spans:
        raise SystemExit("没有读到 span(检查路径 / --trace / --session)")
    g = build(spans)
    g["source"] = {"file": os.path.abspath(src), "session": session, "trace": trace}
    out = out or (path if os.path.isdir(path) else os.path.dirname(os.path.abspath(src)) or ".")
    os.makedirs(out, exist_ok=True)
    jp = os.path.join(out, "forward-path.json")
    mp = os.path.join(out, "forward-path.md")
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(g, f, ensure_ascii=False, indent=2)
        f.write("\n")
    with open(mp, "w", encoding="utf-8") as f:
        f.write(render_md(g))
    s = g["summary"]
    print(f"forward-path: 假设 {s['hypotheses']} · 假设边 {s['parent_edges']} · 工具边 {s['tool_edges']} · "
          f"收口 {s['resolve_edges']} · 未挂 {s['unattached_tools']} → {mp}", file=sys.stderr)
    return mp


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):   # Windows 控制台缺省 GBK,中文会炸
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="span → 正向假设图")
    ap.add_argument("path", help="spans.jsonl / 导出 JSON / 含 spans.jsonl 的目录")
    ap.add_argument("--out", default=None)
    ap.add_argument("--session", default=None)
    ap.add_argument("--trace", default=None)
    a = ap.parse_args(argv)
    run(a.path, a.out, a.session, a.trace)


if __name__ == "__main__":
    main()
