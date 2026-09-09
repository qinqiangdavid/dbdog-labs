#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import unittest
import prematch as pm

EC = {"case": "X", "fix_diff": "absent", "consistency": {"verdict": "holds"}, "dbdog_findings": [{"evidence_id": "E3", "kind": "empty_or_error"}],
      "evidence_chain": [
          {"id": "E1", "name": "执行计划", "tier": "must", "source": "dbdog", "tool": "get_dbdog_database_explain_plans", "outcome": "obtained_match",
           "params": "query_signature=4659a2e1cd2cdb7", "expect_in_output": "Hash Join 套 Aggregate 全扫 t1", "actual": "structure=Aggregate{Hash Join…}"},
          {"id": "E2", "name": "健康信号", "tier": "bonus", "source": "dbdog", "tool": "get_dbdog_database_health_signals", "outcome": "obtained_match", "params": "", "expect_in_output": "无锁"},
          {"id": "E3", "name": "schema", "tier": "bonus", "source": "dbdog", "tool": "get_dbdog_database_schemas", "outcome": "empty_or_error", "params": "t1", "expect_in_output": "索引"},
          {"id": "S1", "name": "源码", "tier": "must", "source": "local_source", "tool": "local_source_tree", "outcome": "obtained_match", "params": "grep convert_OREXISTS", "expect_in_output": "subselect.cpp:6034"},
          {"id": "E4", "name": "explain analyze", "tier": "bonus", "source": "unavailable", "tool": None, "outcome": "no_tool"},
      ]}
FP = {"summary": {"hypotheses": 2}, "nodes": [
    {"id": "H1", "type": "confirm", "text": "能锚定", "verdict": "confirmed", "declared": True, "first_seq": 1,
     "calls": [{"seq": 1, "tool": "search_dbdog_database_samples", "purpose": "找慢 SQL", "status": "ok", "agent": "main"},
               {"seq": 2, "tool": "get_dbdog_database_explain_plans", "purpose": "看签名 4659a2e1cd2cdb7 的执行计划", "status": "ok", "agent": "main"}]},
    {"id": "H2", "type": "cause", "text": "缺索引", "verdict": "open", "declared": True, "first_seq": 3,
     "calls": [{"seq": 3, "tool": "get_dbdog_database_recommendations", "purpose": "看建议", "status": "ok", "agent": "main"},
               {"seq": 4, "tool": "Grep", "purpose": "grep convert_OREXISTS", "status": "ok", "agent": "a1"}]},
], "unattached_tools": [{"seq": 5, "tool": "Bash", "reason": "no_intent"}]}


class Prematch(unittest.TestCase):
    def test_matrix(self):
        m = pm.match(EC, FP)
        rows = {r["id"]: r for r in m["matrix"]}
        self.assertEqual(rows["E1"]["prelim"], "正向已调用(待判:返回是否用上 / 推论是否做了)")
        self.assertEqual(rows["E1"]["forward_hits"][0]["seq"], 2); self.assertEqual(rows["E1"]["forward_hits"][0]["hypothesis"], "H1")
        self.assertGreater(rows["E1"]["forward_hits"][0]["score"], 0)
        self.assertEqual(rows["E2"]["prelim"], "正向未调用(待判:假设没提到 / 工具没选对)")
        self.assertEqual(rows["E3"]["prelim"], "应有结果但没有")
        self.assertEqual(rows["E4"]["prelim"], "无工具")
        self.assertEqual(rows["S1"]["forward_hits"][0]["seq"], 4)   # 源码面对到 Grep
        self.assertEqual([t["tool"] for t in m["forward_only_tools"]], ["search_dbdog_database_samples", "get_dbdog_database_recommendations"])
        self.assertEqual(m["forward"]["tool_calls"], 5); self.assertEqual(m["reverse"]["verdict"], "holds")
        self.assertEqual(m["prelim_counts"]["无工具"], 1)

    def test_tokens(self):
        self.assertIn("执行", pm.tokens("执行计划")); self.assertIn("4659a2e1cd2cdb7", pm.tokens("签名 4659a2e1cd2cdb7"))
        self.assertGreater(pm.overlap("看签名 4659a2e1cd2cdb7 的执行计划", "query_signature=4659a2e1cd2cdb7 执行计划"), 0.3)


if __name__ == "__main__":
    unittest.main()
