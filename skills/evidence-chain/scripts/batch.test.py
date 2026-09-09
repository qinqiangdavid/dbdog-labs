#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import tempfile
import unittest

import batch as b

REPRO = """# 复现用例集

## DTS2026090100123 OR-EXISTS 慢查询
复现开始时间: 2026-09-09 09:04:00
复现结束时间: 2026-09-09 09:07:00
现象: openGauss 业务库 bench 有一条涉及 t0、t1 的查询很慢
### 复现步骤
create table ...

## DTS2026090100456 锁等待
复现时间: 2026-09-09 10:12 ~ 10:15
现象: 大量会话卡住
修复: https://codehub.example.com/r/openGauss/commit/deadbeef

## DTS2026090100789 别的
在 2026-09-09 11:00 到 11:03 之间内存涨
"""

FILTER = """# 根因集

## DTS2026090100456
### 根因
autovacuum 与 DDL 互锁

## DTS2026090100123
### 根因
sublink pull-up 未做代价判断
修复 PR: https://gitee.com/opengauss/openGauss-server/pulls/8080
### 现象量化
- fast 0.514 ms / slow 860 ms
"""


def ws(d, manifest_extra=""):
    open(os.path.join(d, "reproduce.md"), "w", encoding="utf-8").write(REPRO)
    open(os.path.join(d, "filter.md"), "w", encoding="utf-8").write(FILTER)
    os.makedirs(os.path.join(d, "src"), exist_ok=True)
    mp = os.path.join(d, "batch.md")
    open(mp, "w", encoding="utf-8").write("- 复现文件: reproduce.md\n- 根因文件: filter.md\n- 源码树: src\n- 输出目录: out\n- 间隔分钟: 0\n" + manifest_extra)
    return mp


class Batch(unittest.TestCase):
    def test_manifest(self):
        st = b.parse_manifest("# 批次\n- 复现文件: a.md\n- 根因文件: b.md\n- 源码树: /s\n- 输出目录: /o\n")["settings"]
        self.assertEqual(st["phenomenon_file"], "a.md"); self.assertEqual(st["out"], "/o"); self.assertEqual(st["interval_min"], 5.0)

    def test_window(self):
        self.assertEqual(b.extract_window("复现开始时间: 2026-09-09 09:04:00\n复现结束时间: 2026-09-09 09:07:00\n"), "2026-09-09 09:04:00 ~ 2026-09-09 09:07:00")
        self.assertEqual(b.extract_window("- 开始时间:2026/09/09 09:04\n- 结束时间:2026/09/09 09:07"), "2026/09/09 09:04 ~ 2026/09/09 09:07")
        self.assertEqual(b.extract_window("复现时间:2026-09-09 09:04:00 ~ 2026-09-09 09:07:00"), "2026-09-09 09:04:00 ~ 2026-09-09 09:07:00")
        self.assertEqual(b.extract_window("- 执行时间窗: 2026/09/09 09:04–09:07 (UTC+8)"), "2026/09/09 09:04–09:07 (UTC+8)")
        self.assertEqual(b.extract_window("在 2026-09-09 09:04 到 09:07 之间变慢"), "在 2026-09-09 09:04 到 09:07 之间变慢")
        self.assertEqual(b.extract_window("没有时间"), "")

    def test_fix_link(self):
        self.assertEqual(b.extract_fix("根因……\n修复 PR: https://gitee.com/o/r/pulls/8080\n"), "https://gitee.com/o/r/pulls/8080")
        self.assertEqual(b.extract_fix("见 https://codehub.example.com/r/x/commit/deadbeef)。"), "https://codehub.example.com/r/x/commit/deadbeef")
        self.assertEqual(b.extract_fix("补丁链接:https://dts.example.com/issue/1"), "https://dts.example.com/issue/1")
        self.assertEqual(b.extract_fix("无"), "")

    def test_assemble_from_reproduce(self):
        with tempfile.TemporaryDirectory() as d:
            mp = ws(d)
            st = b.parse_manifest(open(mp, encoding="utf-8").read())["settings"]
            absp = lambda p: os.path.join(d, p)
            cases = {c["id"]: c for c in b.assemble(st, absp)}
            self.assertEqual(list(cases), ["DTS2026090100123", "DTS2026090100456", "DTS2026090100789"])
            c1 = cases["DTS2026090100123"]
            self.assertEqual(c1["window"], "2026-09-09 09:04:00 ~ 2026-09-09 09:07:00")
            self.assertEqual(c1["fix"], "https://gitee.com/opengauss/openGauss-server/pulls/8080")   # 根因小节里的链接
            self.assertIn("t0、t1", c1["phenomenon"]); self.assertIn("pull-up", c1["root_cause"]); self.assertNotIn("autovacuum", c1["root_cause"])
            c2 = cases["DTS2026090100456"]
            self.assertEqual(c2["window"], "2026-09-09 10:12 ~ 10:15"); self.assertEqual(c2["fix"], "https://codehub.example.com/r/openGauss/commit/deadbeef")
            self.assertIsNone(cases["DTS2026090100789"]["root_cause"])

    def test_check_and_dry_run(self):
        with tempfile.TemporaryDirectory() as d:
            mp = ws(d)
            problems, notes = b.check(mp)
            self.assertEqual(problems, [], problems)
            self.assertTrue(any("发现 3 个单号" in n for n in notes), notes)
            self.assertTrue(any("DTS2026090100789:根因文件里没有" in n for n in notes))
            rows = {r["id"]: r["status"] for r in b.run_batch(mp, dry_run=True)}
            self.assertEqual(rows, {"DTS2026090100123": "dry_run", "DTS2026090100456": "dry_run", "DTS2026090100789": "skipped_no_root_cause"})
            c1 = os.path.join(d, "out", "DTS2026090100123")
            for f in ("prompt.txt", "root-cause.md", "window.txt", "dry-run.txt"):
                self.assertTrue(os.path.isfile(os.path.join(c1, f)), f)
            dr = open(os.path.join(c1, "dry-run.txt"), encoding="utf-8").read()
            self.assertIn("--out " + c1, dr); self.assertIn("--work " + os.path.join(c1, "work"), dr)
            self.assertIn("--fix https://gitee.com", dr); self.assertIn("--window 2026-09-09 09:04:00 ~", dr)
            summ = open(os.path.join(d, "out", "batch-summary.md"), encoding="utf-8").read()
            self.assertIn("DTS2026090100789 | 跳过:根因文件里没有该单号", summ)
            self.assertTrue(os.path.isfile(os.path.join(d, "out", "batch-summary.json")))

    def test_tickets_file_limits_and_overrides(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "tickets.txt"), "w", encoding="utf-8").write("DTS2026090100456 D:\\fixes\\456.diff 2026-09-09 10:00–10:03\n")
            mp = ws(d, "- 问题单文件: tickets.txt\n")
            st = b.parse_manifest(open(mp, encoding="utf-8").read())["settings"]
            cases = b.assemble(st, lambda p: os.path.join(d, p))
            self.assertEqual([c["id"] for c in cases], ["DTS2026090100456"])
            self.assertEqual(cases[0]["fix"], "D:\\fixes\\456.diff"); self.assertEqual(cases[0]["window"], "2026-09-09 10:00–10:03")

    def test_missing_files(self):
        with tempfile.TemporaryDirectory() as d:
            mp = os.path.join(d, "batch.md"); open(mp, "w", encoding="utf-8").write("- 复现文件: nope.md\n- 根因文件: nope2.md\n")
            problems, _ = b.check(mp)
            self.assertTrue(any("复现文件不存在" in p for p in problems)); self.assertTrue(any("缺「输出目录」" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
