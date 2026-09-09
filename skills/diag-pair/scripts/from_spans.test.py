#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import os
import tempfile
import unittest

import from_spans as fs


def tool(span_id, name, ts, intent=None, tags=None, **kw):
    s = {"span_id": span_id, "kind": "tool", "name": name, "trace_id": "aa", "ts": ts, "tags": tags or {}}
    if intent is not None:
        s["intent"] = intent
    s.update(kw)
    return s


def llm(span_id, ts, output=None, output_local=None, thinking_local=None, kind="llm"):
    s = {"span_id": span_id, "kind": kind, "name": "anthropic.messages", "trace_id": "aa", "ts": ts, "tags": {}}
    if output is not None:
        s["output"] = output
    if output_local is not None:
        s["output_local"] = output_local
    if thinking_local is not None:
        s["thinking_local"] = thinking_local
    return s


class FromSpans(unittest.TestCase):
    def test_tags_build_parent_and_tool_edges(self):
        spans = [
            tool("t1", "get_dbdog_metric", 1, tags={"hypothesis_id": "H1", "hypothesis_type": "confirm", "hypothesis": "能锚定"}),
            tool("t2", "search_dbdog_logs", 2, tags={"hypothesis_id": "H2", "parent_hypothesis_id": "H1",
                                                    "hypothesis_type": "cause", "hypothesis": "计划形状错"}),
            tool("t3", "Bash", 3),
        ]
        g = fs.build(spans)
        ids = [n["id"] for n in g["nodes"]]
        self.assertEqual(ids, ["H1", "H2"])
        self.assertEqual(g["nodes"][1]["parent"], "H1")
        kinds = {(e["kind"], e.get("from"), e.get("to") or e.get("tool")) for e in g["edges"]}
        self.assertIn(("parent", "H1", "H2"), kinds)
        self.assertIn(("tool", "H1", "get_dbdog_metric"), kinds)
        self.assertIn(("tool", "H2", "search_dbdog_logs"), kinds)
        self.assertEqual(len(g["unattached_tools"]), 1)
        self.assertEqual(g["unattached_tools"][0]["tool"], "Bash")

    def test_intent_fallback_and_placeholder_parent(self):
        g = fs.build([tool("t1", "get_dbdog_metric", 1, intent="[H3<H2] 类型=根因; 假设=短路失败; 判据=计划里有全扫则成立")])
        self.assertEqual(g["nodes"][0]["id"], "H2")
        self.assertFalse(g["nodes"][0]["declared"])   # 只被引用,从未在调用上声明
        self.assertEqual(g["nodes"][1]["id"], "H3")
        self.assertTrue(g["nodes"][1]["declared"])
        self.assertEqual(g["nodes"][1]["parent"], "H2")
        self.assertEqual(g["nodes"][1]["type"], "cause")
        self.assertEqual(g["nodes"][1]["expect"], "计划里有全扫则成立")

    def test_resolve_edges_and_verdict(self):
        g = fs.build([
            tool("t1", "get_dbdog_metric", 1, intent="[H1] 类型=现象确认; 假设=有慢查询; 判据=有则成立"),
            tool("t2", "get_dbdog_metric", 2, intent="[H2<H1] 类型=根因; 假设=计划错; 判据=全扫; 关=H1:证实; 意图=看计划"),
        ])
        by = {n["id"]: n for n in g["nodes"]}
        self.assertEqual(by["H1"]["verdict"], "confirmed")
        res = [e for e in g["edges"] if e["kind"] == "resolve"]
        self.assertEqual(res, [{"kind": "resolve", "from": "H2", "to": "H1", "verdict": "confirmed", "span_id": "t2"}])

    def test_unattached_reasons(self):
        g = fs.build([
            tool("t1", "load_dbdog_skill", 1, intent="类型=根因; 假设=<资源饱和>; 判据=<CPU 冲高>"),
            tool("t2", "Bash", 2),
            tool("t3", "get_dbdog_metric", 3, intent=""),
        ])
        reasons = {u["span_id"]: u["reason"] for u in g["unattached_tools"]}
        self.assertEqual(reasons, {"t1": "intent_without_head", "t2": "no_intent", "t3": "no_intent"})
        self.assertEqual(g["summary"]["unattached_intent_without_head"], 1)

    def test_calls_carry_seq_intent_agent(self):
        g = fs.build([
            tool("t1", "get_dbdog_metric", "2026-09-09T01:00:00Z", intent="[H1] 假设=a; 判据=b; 意图=看指标"),
            tool("t2", "ddsql_run_query", "2026-09-09T01:00:05Z", intent="[H1] 判据=b; 意图=查 SQL",
                 tags={"agent_id": "abcdef1234"}),
        ])
        calls = g["nodes"][0]["calls"]
        self.assertEqual([c["seq"] for c in calls], [1, 2])
        self.assertEqual(calls[0]["purpose"], "看指标")
        self.assertEqual(calls[0]["agent"], "main")
        self.assertEqual(calls[1]["agent"], "abcdef12")
        self.assertEqual(g["nodes"][0]["first_seq"], 1)

    def test_render_md(self):
        g = fs.build([
            tool("t1", "get_dbdog_metric", 1, intent="[H1] 类型=现象确认; 假设=有慢查询; 判据=有则成立; 意图=找慢 SQL"),
            tool("t2", "get_dbdog_metric", 2, intent="[H2.1<H2] 类型=根因; 假设=计划错; 判据=全扫; 关=H1:证实; 意图=看计划"),
            tool("t3", "load_dbdog_skill", 3, intent="类型=根因; 假设=<饱和>"),
            tool("t4", "Bash", 4),
        ])
        md = fs.render_md(g)
        for needle in ("有慢查询", "判据:有则成立", "找慢 SQL", "H2", "未声明", "H2.1 → H1", "证实",
                       "intent 不带 [H..] 头", "Bash", "## 假设出现顺序"):
            self.assertIn(needle, md, needle)

    # —— 正文「提出」事件(2026-09-08):hook 只采原文不解析,父节点文本从 llm span 正文里提 ——
    def test_prose_proposal_fills_undeclared_parent(self):
        g = fs.build([
            llm("l1", 1, output="先分派。\n提出 [H2] 类型=根因; 假设=连接池耗尽; 判据=active 连接数贴上限则成立\n再派子代理。"),
            tool("t1", "get_dbdog_metric", 2, intent="[H2.1<H2] 类型=根因; 假设=池被慢事务占住; 判据=长事务>30s"),
        ])
        by = {n["id"]: n for n in g["nodes"]}
        self.assertEqual(by["H2"]["text"], "连接池耗尽")
        self.assertEqual(by["H2"]["type"], "cause")
        self.assertEqual(by["H2"]["expect"], "active 连接数贴上限则成立")
        self.assertFalse(by["H2"]["declared"])            # 仍没有调用以 [H2] 开头
        self.assertEqual(by["H2"]["proposed_in"], {"span_id": "l1", "in": "output"})
        self.assertEqual(g["summary"]["proposed_in_prose"], 1)

    def test_prose_prefers_output_local_and_scans_thinking_local(self):
        g = fs.build([
            llm("l1", 1, output="截断了的正文…",
                output_local="截断了的正文…\n提出 [H3] 类型=根因; 假设=WAL 刷盘慢",
                thinking_local="想想。\n提出 [H4<H3] 类型=根因; 假设=磁盘被别的进程占满"),
        ])
        by = {n["id"]: n for n in g["nodes"]}
        self.assertEqual(by["H3"]["text"], "WAL 刷盘慢")
        self.assertEqual(by["H3"]["proposed_in"]["in"], "output")
        self.assertEqual(by["H4"]["text"], "磁盘被别的进程占满")
        self.assertEqual(by["H4"]["parent"], "H3")
        self.assertEqual(by["H4"]["proposed_in"]["in"], "thinking")
        kinds = {(e["kind"], e.get("from"), e.get("to")) for e in g["edges"]}
        self.assertIn(("parent", "H3", "H4"), kinds)

    def test_prose_skips_protocol_restatement_and_keeps_first(self):
        g = fs.build([
            llm("l1", 1, output="书写约定:在正文里提出新假设时写「提出 [H2] 类型=根因; 假设=…」\n"
                              "示例:提出 [H9] 类型=根因; 假设=<占位>\n"
                              "提出 [H2] 类型=根因; 假设=真的假设"),
            llm("l2", 2, output="提出 [H2] 类型=根因; 假设=后来改口的"),
        ])
        by = {n["id"]: n for n in g["nodes"]}
        self.assertEqual(sorted(by), ["H2"])               # 复述约定与示例行不算提出
        self.assertEqual(by["H2"]["text"], "真的假设")     # 最早一次为准
        self.assertEqual(by["H2"]["proposed_in"]["span_id"], "l1")

    def test_render_md_shows_prose_proposal(self):
        g = fs.build([
            llm("l1", 1, output="提出 [H2] 类型=根因; 假设=连接池耗尽; 判据=贴上限"),
            tool("t1", "get_dbdog_metric", 2, intent="[H2.1<H2] 类型=根因; 假设=慢事务; 判据=>30s"),
        ])
        md = fs.render_md(g)
        self.assertIn("连接池耗尽", md)
        self.assertIn("提出于正文", md)
        self.assertNotIn("hook 采不到", md)

    def test_head_tolerates_trailing_gt_on_parent(self):
        # 模板 [H<编号><H<父编号>] 被照抄成 [H2.1<H2>](2026-09-09 一轮 21 次),父编号后多个 >
        p = fs.parse_intent("[H2.1<H2>] 类型=根因; 假设=扫描量对不上; 判据=temp_bytes 反推")
        self.assertEqual((p["id"], p["parent"], p["text"]), ("H2.1", "H2", "扫描量对不上"))
        self.assertEqual(fs.parse_intent("[ H4.1 < H4 > ] 假设=x")["parent"], "H4")

    def test_dir_resolves_spans_jsonl(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "spans.jsonl")
            with open(p, "w", encoding="utf-8") as f:
                f.write(json.dumps({"span_id": "x", "kind": "agent", "trace_id": "cc"}) + "\n")
            self.assertEqual(fs.resolve_input(d), p)


if __name__ == "__main__":
    unittest.main()
