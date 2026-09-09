#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import os
import tempfile
import unittest

import batch as b


def mk(d, cid, comp=None, both=True):
    p = os.path.join(d, cid); os.makedirs(p, exist_ok=True)
    open(os.path.join(p, "evidence-chain.json"), "w").write("{}")
    if both:
        open(os.path.join(p, "forward-path.json"), "w").write("{}")
    if comp is not None:
        json.dump(comp, open(os.path.join(p, "compare.json"), "w", encoding="utf-8"), ensure_ascii=False)


class Batch(unittest.TestCase):
    def test_ready_and_aggregate(self):
        with tempfile.TemporaryDirectory() as d:
            mk(d, "DTS1", {"counts": {"无工具": 1, "应有结果但没有": 2}, "main_layer": "dbdog", "reverse_verdict": "holds", "root_cause_pinned_by_agent": True,
                           "first_wrong_step": {"seq": 12},
                           "improvements": [{"category": "采集面", "title": "schema 采集未开启", "tool": "get_dbdog_database_schemas", "evidence": ["E9"], "fix": "开 schema 采集", "impact": "lose_cross_check"},
                                            {"category": "工具契约", "title": "recommendations 对 openGauss 零产出", "tool": "get_dbdog_database_recommendations", "evidence": ["E6"], "fix": "…", "impact": "misleads_agent"}]})
            mk(d, "DTS2", {"counts": {"假设没提到": 1}, "main_layer": "agent", "reverse_verdict": "partial", "root_cause_pinned_by_agent": False,
                           "improvements": [{"category": "采集面", "title": "Schema 采集未开启 (t1)", "tool": "get_dbdog_database_schemas", "evidence": ["E12"], "fix": "开 schema 采集", "impact": "lose_cross_check"},
                                            {"category": "别的", "title": "分诊缺 LIKE 前缀这一档", "tool": None, "evidence": ["seq 7"], "fix": "SOP 加枚举", "impact": "misleads_agent"}]})
            mk(d, "DTS3", both=False)
            self.assertEqual(b.ready_cases(d), ["DTS1", "DTS2"])
            md, ranked, per = b.aggregate(d, ["DTS1", "DTS2"])
            self.assertEqual(ranked[0]["title"], "schema 采集未开启"); self.assertEqual(ranked[0]["cases"], ["DTS1", "DTS2"])   # 大小写/括号归一后合并
            self.assertEqual(len(ranked), 3)
            self.assertEqual([g["category"] for g in ranked if g["title"] == "分诊缺 LIKE 前缀这一档"], ["skill方法论"])   # 非法类别归到 skill方法论
            text = open(md, encoding="utf-8").read()
            self.assertIn("2:DTS1,DTS2", text); self.assertIn("| DTS1 | 是 | holds | dbdog | 12 |", text)
            self.assertTrue(os.path.isfile(os.path.join(d, "improvements.json")))


if __name__ == "__main__":
    unittest.main()
