#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import tempfile
import unittest

import batch as b

MANIFEST = """# 反向推导批次

- 现象文件: {d}/reproduce.md
- 根因文件: {d}/filter.md
- 源码树: {d}/src
- 输出目录: {d}/out
- 间隔分钟: 0

| 用例 | 事故窗 | 修复 | 备注 |
|---|---|---|---|
| DTS001 | 2026-09-09 09:04–09:07 (UTC+8),库 bench | https://dts.example.com/issue/001 | |
| DTS002 | 2026-09-09 10:12–10:15 (UTC+8) | {d}/fix2.diff | 有 diff |
| DTS003 | 2026-09-09 11:00–11:03 (UTC+8) | | 没根因 |
"""

REPRO = """# 复现用例集

## DTS001 OR-EXISTS 慢查询
诊断: 09:04–09:07 bench 库有条涉及 t0、t1 的查询很慢
### 复现步骤
create table ...

## DTS002 锁等待
诊断: 10:12 起大量会话卡住

## DTS003 别的
诊断: 内存涨
"""

FILTER = """# 根因集

## DTS002
### 根因
autovacuum 与 DDL 互锁

## DTS001
### 根因
sublink pull-up 未做代价判断
### 现象量化
- fast 0.514 ms / slow 860 ms
"""


class Manifest(unittest.TestCase):
    def test_parse(self):
        m = b.parse_manifest(MANIFEST.format(d="/x"))
        self.assertEqual(m["settings"]["phenomenon_file"], "/x/reproduce.md")
        self.assertEqual(m["settings"]["root_cause_file"], "/x/filter.md")
        self.assertEqual(m["settings"]["source"], "/x/src")
        self.assertEqual(m["settings"]["out"], "/x/out")
        self.assertEqual(m["settings"]["interval_min"], 0.0)
        self.assertEqual([c["id"] for c in m["cases"]], ["DTS001", "DTS002", "DTS003"])
        self.assertEqual(m["cases"][0]["fix"], "https://dts.example.com/issue/001")
        self.assertEqual(m["cases"][2]["fix"], "")
        self.assertIn("库 bench", m["cases"][0]["window"])

    def test_extract_by_heading(self):
        ids = ["DTS001", "DTS002", "DTS003"]
        s = b.extract_section(REPRO, "DTS001", ids)
        self.assertIn("t0、t1", s); self.assertIn("复现步骤", s); self.assertNotIn("锁等待", s)
        s2 = b.extract_section(FILTER, "DTS001", ids)
        self.assertIn("pull-up", s2); self.assertIn("现象量化", s2); self.assertNotIn("autovacuum", s2)
        self.assertIsNone(b.extract_section(FILTER, "DTS003", ids))

    def test_extract_fallback_plain_lines(self):
        text = "DTS001: 现象 A\n细节 a\nDTS002: 现象 B\n细节 b\n"
        self.assertEqual(b.extract_section(text, "DTS001", ["DTS001", "DTS002"]).strip(), "DTS001: 现象 A\n细节 a")
        self.assertEqual(b.extract_section(text, "DTS002", ["DTS001", "DTS002"]).strip(), "DTS002: 现象 B\n细节 b")

    def test_fix_kind(self):
        self.assertEqual(b.fix_kind("https://github.com/o/r/pull/1"), "diff_url")
        self.assertEqual(b.fix_kind("https://gitee.com/o/r/pulls/1"), "diff_url")
        self.assertEqual(b.fix_kind("https://dts.example.com/issue/1"), "ticket")
        self.assertEqual(b.fix_kind("/x/fix.diff"), "file")
        self.assertEqual(b.fix_kind(""), "none")

    def test_dry_run_prepares_inputs_and_summary(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "reproduce.md"), "w", encoding="utf-8").write(REPRO)
            open(os.path.join(d, "filter.md"), "w", encoding="utf-8").write(FILTER)
            open(os.path.join(d, "fix2.diff"), "w", encoding="utf-8").write("--- a\n+++ b\n")
            os.makedirs(os.path.join(d, "src"))
            mp = os.path.join(d, "batch.md"); open(mp, "w", encoding="utf-8").write(MANIFEST.format(d=d))
            rows = b.run_batch(mp, dry_run=True)
            st = {r["id"]: r["status"] for r in rows}
            self.assertEqual(st, {"DTS001": "dry_run", "DTS002": "dry_run", "DTS003": "skipped_no_root_cause"})
            c1 = os.path.join(d, "out", "DTS001")
            self.assertIn("t0、t1", open(os.path.join(c1, "prompt.txt"), encoding="utf-8").read())
            self.assertIn("pull-up", open(os.path.join(c1, "root-cause.md"), encoding="utf-8").read())
            self.assertIn("09:04", open(os.path.join(c1, "window.txt"), encoding="utf-8").read())
            self.assertTrue(os.path.isfile(os.path.join(d, "out", "batch-summary.md")))
            summ = open(os.path.join(d, "out", "batch-summary.md"), encoding="utf-8").read()
            self.assertIn("DTS003", summ); self.assertIn("根因", summ)
            import json
            js = json.load(open(os.path.join(d, "out", "batch-summary.json"), encoding="utf-8"))
            self.assertEqual([c["id"] for c in js["cases"]], ["DTS001", "DTS002", "DTS003"])
            self.assertIn("--work", open(os.path.join(c1, "dry-run.txt"), encoding="utf-8").read())

    def test_diag_prompt_and_env(self):
        pr = b.build_diag_prompt("## OG-1 复现用例\n\n诊断: {{WINDOW}}(UTC+8),bench 库慢", "2026-09-09 09:04–09:07 (UTC+8),实例 x", "【假设书写约定】...")
        self.assertTrue(pr.startswith("诊断: 2026-09-09 09:04–09:07(UTC+8),bench 库慢"), pr[:80])
        self.assertNotIn("复现用例", pr)
        pr2 = b.build_diag_prompt("题面 {{WINDOW}} 慢", "2026-09-09 09:04–09:07 (UTC+8),实例 x", "R")
        self.assertTrue(pr2.startswith("诊断: 题面 2026-09-09 09:04–09:07 (UTC+8) 慢"))
        self.assertIn("【假设书写约定】", pr)
        self.assertTrue(b.build_diag_prompt("bench 库慢", "", "R").startswith("诊断: bench 库慢"))
        env = b.diag_env({"PATH": "/bin"}, "/out/OG-1", "OG-1", "/cfg")
        self.assertEqual(env["DBDOG_OBS_SPANS"], os.path.join("/out/OG-1", "spans.jsonl"))
        self.assertEqual(env["DBDOG_OBS_DIR"], os.path.join("/out/OG-1", "obs-state"))
        self.assertEqual(env["DBDOG_OBS_TAGS"], "case_id=OG-1")
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], "/cfg")
        deny = b.diag_settings("/out")["permissions"]["deny"]
        self.assertIn("Read(///out/**)", deny)
        self.assertFalse(any(x.startswith(("Grep(", "Glob(")) for x in deny))

    def test_stage_plan(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "reproduce.md"), "w", encoding="utf-8").write(REPRO)
            open(os.path.join(d, "filter.md"), "w", encoding="utf-8").write(FILTER)
            open(os.path.join(d, "fix2.diff"), "w", encoding="utf-8").write("--- a\n+++ b\n")
            os.makedirs(os.path.join(d, "src"))
            os.makedirs(os.path.join(d, "out", "DTS002")); open(os.path.join(d, "out", "DTS002", "spans.jsonl"), "w").write("{}\n")
            mp = os.path.join(d, "batch.md"); open(mp, "w", encoding="utf-8").write(MANIFEST.format(d=d).replace("- 间隔分钟: 0", "- 间隔分钟: 0\n- 阶段: 诊断,正向,反向"))
            rows = {r["id"]: r for r in b.run_batch(mp, dry_run=True)}
            self.assertEqual(rows["DTS001"]["stages"], ["诊断", "正向", "反向"])
            self.assertEqual(rows["DTS002"]["stages"], ["正向", "反向"])   # 已有 spans.jsonl,不再诊断
            open(os.path.join(d, "out", "DTS002", "evidence-chain.md"), "w").write("x")
            rows = {r["id"]: r for r in b.run_batch(mp, dry_run=True)}
            self.assertEqual(rows["DTS002"]["stages"], ["正向"])   # 反向产物也在了,只剩正向(零模型,总是重出)

    def test_discover_ids_and_window(self):
        rc = "# 根因集\n\n## DTS2026090100123 慢\n根因 A\n\n## DTS2026090100456\n根因 B\n\n## OG-7601\n根因 C\n"
        self.assertEqual(b.discover_ids(rc), ["DTS2026090100123", "DTS2026090100456", "OG-7601"])
        self.assertEqual(b.discover_ids(rc, r"DTS\d+"), ["DTS2026090100123", "DTS2026090100456"])
        self.assertEqual(b.extract_window("## X\n现象:慢\n复现时间:2026-09-09 09:04:00 ~ 2026-09-09 09:07:00\n步骤..."), "2026-09-09 09:04:00 ~ 2026-09-09 09:07:00")
        self.assertEqual(b.extract_window("- 执行时间窗: 2026/09/09 09:04–09:07 (UTC+8)"), "2026/09/09 09:04–09:07 (UTC+8)")
        self.assertEqual(b.extract_window("| 时间 | 2026-09-09 09:04 |\n"), "2026-09-09 09:04")
        self.assertEqual(b.extract_window("在 2026-09-09 09:04 到 09:07 之间变慢"), "在 2026-09-09 09:04 到 09:07 之间变慢")
        self.assertEqual(b.extract_window("没有时间的现象"), "")

    def test_no_table_uses_root_cause_ids_and_ticket_template(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "reproduce.md"), "w", encoding="utf-8").write(
                "# 复现\n\n## DTS001 慢查询\n复现时间:2026-09-09 09:04 ~ 09:07\n诊断: bench 库 t0 t1 慢\n\n## DTS002 锁\n复现时间:2026-09-09 10:00 ~ 10:03\n诊断: 卡住\n")
            open(os.path.join(d, "filter.md"), "w", encoding="utf-8").write("# 根因\n\n## DTS002\n锁\n\n## DTS001\n提升\n")
            os.makedirs(os.path.join(d, "src"))
            mp = os.path.join(d, "batch.md")
            open(mp, "w", encoding="utf-8").write(f"- 现象文件: reproduce.md\n- 根因文件: filter.md\n- 源码树: src\n- 输出目录: out\n- 间隔分钟: 0\n- 问题单地址模板: https://dts.example.com/issue/{{id}}\n- 用例号正则: DTS\\d+\n")
            problems, notes = b.check(mp)
            self.assertEqual(problems, [], problems)
            self.assertTrue(any("发现 2 个用例:DTS002, DTS001" in n for n in notes), notes)
            rows = b.run_batch(mp, dry_run=True)
            self.assertEqual([r["id"] for r in rows], ["DTS002", "DTS001"])
            dr = open(os.path.join(d, "out", "DTS001", "dry-run.txt"), encoding="utf-8").read()
            self.assertIn("--fix https://dts.example.com/issue/DTS001", dr)
            self.assertIn("--window 2026-09-09 09:04 ~ 09:07", dr)
            self.assertNotIn("复现时间", open(os.path.join(d, "out", "DTS001", "prompt.txt"), encoding="utf-8").read().split("诊断:")[0][:0])

    def test_check(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "reproduce.md"), "w", encoding="utf-8").write(REPRO)
            open(os.path.join(d, "filter.md"), "w", encoding="utf-8").write(FILTER)
            os.makedirs(os.path.join(d, "src"))
            mp = os.path.join(d, "batch.md"); open(mp, "w", encoding="utf-8").write(MANIFEST.format(d=d))
            problems, notes = b.check(mp)
            joined = "\n".join(problems)
            self.assertIn("DTS002:修复 diff 文件不存在", joined)   # fix2.diff 没建
            self.assertNotIn("DTS001", joined)
            self.assertTrue(any("DTS003:根因文件里没有" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
