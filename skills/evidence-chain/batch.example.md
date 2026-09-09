# 反向推导批次(示例,复制后改路径;Windows 路径直接写反斜杠即可)

- 现象文件: D:\pair\inputs\reproduce.md        ← 多用例复现文件:每个用例一个小节,含现象与复现时间
- 根因文件: D:\pair\inputs\filter.md           ← 多用例根因文件:每个用例一个小节;这里有的用例才跑
- 源码树: D:\repo\opengauss-server
- 输出目录: D:\pair\out
- 问题单地址模板: https://dts.example.com/issue/{id}   ← 按单号拼出问题单网页,skill 去页面里找修复代码 / 代码链接
- 问题单请求头文件: D:\pair\dts-headers.txt          ← 可选:页面要登录时,每行 "Cookie: …" 之类;别入库
- 用例号正则: DTS\d+                                  ← 可选:缺省认 DTS 单号 / OG-数字 / 大写字母-数字
- 间隔分钟: 5
- 阶段: 正向,反向                                     ← 写 诊断,正向,反向 则连正向诊断一起自动跑
- 模型:
- 模型档:
- MCP配置:
- 诊断模型档:
- 诊断MCP配置:

下面这张表**可以整个不写**:不写就跑根因文件里出现的全部用例,事故窗从复现小节解析,修复按问题单模板拼。
要覆盖某个用例的窗 / 修复来源 / span 文件时再列一行:

| 用例 | 事故窗 | 修复 | span 文件 | 备注 |
|---|---|---|---|---|
| DTS2026090100456 | | https://gitee.com/opengauss/openGauss-server/pulls/8080 | | 这单的修复走 PR 链接,不看问题单页 |
| DTS2026090100789 | 2026-09-09 11:00–11:03 (UTC+8),实例 opengauss-xxx,库 bench | D:\pair\inputs\fixes\789.diff | D:\spans\789\spans.jsonl | 手填窗 + 本地 diff + 已有 span |
