# 反向推导批次(示例,复制后改路径;Windows 路径直接写反斜杠即可)

- 现象文件: D:\cases\reproduce.md
- 根因文件: D:\cases\filter.md
- 源码树: D:\repo\opengauss-server
- 输出目录: D:\pair\out
- 间隔分钟: 5
- 模型:
- 模型档:
- MCP配置:

| 用例 | 事故窗 | 修复 | 备注 |
|---|---|---|---|
| DTS2026090100123 | 2026-09-09 09:04–09:07 (UTC+8),实例 opengauss-xxx,库 bench | https://dts.example.com/issue/123 | 修复代码在单子网页里 |
| DTS2026090100456 | 2026-09-09 10:12–10:15 (UTC+8),实例 opengauss-xxx,库 bench | https://gitee.com/opengauss/openGauss-server/pulls/8080 | PR 链接,自动取 .diff |
| DTS2026090100789 | 2026-09-09 11:00–11:03 (UTC+8),实例 opengauss-xxx,库 bench | D:\cases\789.diff | 本地 diff |
| DTS2026090100999 | 2026-09-09 12:00–12:03 (UTC+8),实例 opengauss-xxx,库 bench | | 没有修复代码,按 fix_diff: absent |
