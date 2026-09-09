#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import os
import tempfile
import unittest

import check_chain as cc
import run as ec


class Inputs(unittest.TestCase):
    def _files(self, d):
        p = os.path.join(d, "phenomenon.txt"); open(p, "w", encoding="utf-8").write("题面正文")
        g = os.path.join(d, "root-cause.md"); open(g, "w", encoding="utf-8").write("# GT\n\n## 现象量化\n0.5ms → 860ms\n\n## 根因\nOR-EXISTS 提升\n")
        f = os.path.join(d, "fix.diff"); open(f, "w", encoding="utf-8").write("--- a/x.cpp\n+++ b/x.cpp\n@@ -1 +1 @@\n-a\n+b\n")
        src = os.path.join(d, "src"); os.makedirs(src)
        return p, g, f, src

    def test_prepare_workdir(self):
        with tempfile.TemporaryDirectory() as d:
            p, g, f, src = self._files(d)
            work = ec.prepare_workdir(phenomenon=p, root_cause=g, fix_text=open(f, encoding="utf-8").read(), source=src, work=os.path.join(d, "w"), window="2026-09-09 09:04–09:07")
            case = open(os.path.join(work, "case.md"), encoding="utf-8").read()
            self.assertIn("题面正文", case)
            self.assertIn("09:04–09:07", case)
            self.assertIn("0.5ms → 860ms", case)
            self.assertIn("OR-EXISTS", open(os.path.join(work, "root-cause.md"), encoding="utf-8").read())
            self.assertIn("+++ b/x.cpp", open(os.path.join(work, "fix.diff"), encoding="utf-8").read())
            self.assertEqual(open(os.path.join(work, "source-tree.txt"), encoding="utf-8").read().strip(), os.path.abspath(src))
            self.assertTrue(os.path.isfile(os.path.join(work, "tool-catalog.json")))

    def test_no_fix_no_source(self):
        with tempfile.TemporaryDirectory() as d:
            p, g, f, src = self._files(d)
            work = ec.prepare_workdir(phenomenon=p, root_cause=g, fix_text=None, source=None, work=os.path.join(d, "w"))
            self.assertFalse(os.path.exists(os.path.join(work, "fix.diff")))
            self.assertFalse(os.path.exists(os.path.join(work, "source-tree.txt")))

    def test_fix_url_mapping(self):
        self.assertEqual(ec.diff_url("https://github.com/o/r/pull/12"), "https://github.com/o/r/pull/12.diff")
        self.assertEqual(ec.diff_url("https://gitee.com/opengauss/openGauss-server/pulls/8080"), "https://gitee.com/opengauss/openGauss-server/pulls/8080.diff")
        self.assertEqual(ec.diff_url("https://x/y.diff"), "https://x/y.diff")
        self.assertEqual(ec.diff_url("https://github.com/o/r/commit/abc123"), "https://github.com/o/r/commit/abc123.diff")
        self.assertIsNone(ec.diff_url("/local/fix.diff"))

    def test_command_and_env(self):
        cmd = ec.claude_command("PROMPT", "claude")
        self.assertEqual(cmd[:3], ["claude", "-p", "PROMPT"])
        self.assertNotIn("--mcp-config", cmd)
        cmd2 = ec.claude_command("PROMPT", "claude", "/m.json")
        self.assertEqual(cmd2[cmd2.index("--mcp-config") + 1], "/m.json")
        self.assertIn("--strict-mcp-config", cmd2)
        self.assertIn("--dangerously-skip-permissions", cmd)
        self.assertIn("Bash", cmd[cmd.index("--disallowedTools") + 1])
        env = ec.claude_env({"DBDOG_OBS_REPORT_URL": "http://x", "PATH": "/bin"}, "/cfg")
        self.assertEqual(env["DBDOG_OBS_REPORT_URL"], "")
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], "/cfg")
        self.assertEqual(env["PATH"], "/bin")


class Check(unittest.TestCase):
    catalog = {"get_dbdog_metric", "get_dbdog_database_explain_plans", "local_source_tree"}

    def test_valid_chain(self):
        chain = {"evidence_chain": [
            {"id": "E1", "source": "dbdog", "tool": "get_dbdog_metric", "outcome": "obtained_match"},
            {"id": "E2", "source": "local_source", "tool": "local_source_tree", "outcome": "obtained_match"},
            {"id": "E3", "source": "unavailable", "tool": None, "outcome": "no_tool", "needed_capability": "会话级 GUC", "closest_tool": "get_dbdog_metric", "why_insufficient": "没有 GUC 面"},
            {"id": "E4", "source": "dbdog", "tool": "get_dbdog_database_explain_plans", "outcome": "empty_or_error"},
        ], "dbdog_findings": [{"evidence_id": "E3", "kind": "no_tool"}, {"evidence_id": "E4", "kind": "empty_or_error"}]}
        r = cc.check(chain, self.catalog)
        self.assertEqual(r["problems"], [])
        self.assertEqual(r["counts"], {"dbdog": 2, "local_source": 1, "unavailable": 1})
        self.assertEqual(r["gaps"], ["E3"])
        self.assertEqual(r["outcomes"], {"obtained_match": 2, "obtained_mismatch": 0, "empty_or_error": 1, "no_tool": 1})

    def test_bad_tool_and_missing_fields(self):
        chain = {"evidence_chain": [
            {"id": "E1", "source": "dbdog", "tool": "查一下计划", "outcome": "obtained_match"},
            {"id": "E2", "source": "unavailable", "tool": None, "outcome": "no_tool"},
            {"id": "E3", "tool": "get_dbdog_metric"},
            {"id": "E4", "source": "dbdog", "tool": "get_dbdog_metric"},
            {"id": "E5", "source": "dbdog", "tool": "get_dbdog_metric", "outcome": "obtained_mismatch"},
        ], "dbdog_findings": []}
        r = cc.check(chain, self.catalog)
        joined = "\n".join(r["problems"])
        self.assertIn("E1", joined); self.assertIn("查一下计划", joined)
        self.assertIn("E2", joined); self.assertIn("needed_capability", joined)
        self.assertIn("E3", joined); self.assertIn("source", joined)
        self.assertIn("E4: outcome 缺失", joined)
        self.assertIn("E5: outcome=obtained_mismatch 却没有进 dbdog_findings", joined)
        self.assertIn("E2: source=unavailable 却没有进 dbdog_findings", joined)


if __name__ == "__main__":
    unittest.main()
