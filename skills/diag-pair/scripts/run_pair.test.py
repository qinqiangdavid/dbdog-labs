#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import tempfile
import unittest

import run_pair as rp


class Reverse(unittest.TestCase):
    def _files(self, d):
        p = os.path.join(d, "prompt.txt"); open(p, "w", encoding="utf-8").write("题面正文")
        g = os.path.join(d, "ground-truth.md"); open(g, "w", encoding="utf-8").write("# GT\n\n## 现象量化\n0.5ms → 860ms\n\n## 根因\nOR-EXISTS 提升\n")
        src = os.path.join(d, "src"); os.makedirs(src)
        return p, g, src

    def test_prepare_workdir(self):
        with tempfile.TemporaryDirectory() as d:
            p, g, src = self._files(d)
            work = rp.prepare_reverse_workdir(prompt=p, root_cause=g, source=src, fix=None, work=os.path.join(d, "w"))
            case = open(os.path.join(work, "case.md"), encoding="utf-8").read()
            self.assertIn("题面正文", case)
            self.assertIn("0.5ms → 860ms", case)
            self.assertIn("OR-EXISTS", open(os.path.join(work, "root-cause.md"), encoding="utf-8").read())
            self.assertEqual(open(os.path.join(work, "source-tree.txt"), encoding="utf-8").read().strip(), os.path.abspath(src))
            self.assertTrue(os.path.isfile(os.path.join(work, "tool-catalog.json")))
            self.assertFalse(os.path.exists(os.path.join(work, "fix.diff")))

    def test_case_dir_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            p, g, src = self._files(d)
            args = rp.parse_args(["reverse", d, "--source", src])
            r = rp.resolve_reverse_args(args)
            self.assertEqual(r["prompt"], p)
            self.assertEqual(r["root_cause"], g)
            self.assertEqual(r["out"], d)

    def test_claude_cmd_and_env(self):
        cmd = rp.reverse_command("PROMPT", claude_bin="claude")
        self.assertEqual(cmd[:3], ["claude", "-p", "PROMPT"])
        self.assertIn("--dangerously-skip-permissions", cmd)
        i = cmd.index("--disallowedTools")
        self.assertIn("Bash", cmd[i + 1])
        env = rp.reverse_env({"DBDOG_OBS_REPORT_URL": "http://x", "PATH": "/bin"}, config_dir="/cfg")
        self.assertEqual(env["DBDOG_OBS_REPORT_URL"], "")
        self.assertEqual(env["DBDOG_OBS_TAGS"], "")
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], "/cfg")
        self.assertEqual(env["PATH"], "/bin")


if __name__ == "__main__":
    unittest.main()
