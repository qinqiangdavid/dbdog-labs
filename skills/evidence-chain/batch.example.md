# 反向取证批次(示例,复制后改路径;Windows 路径直接写反斜杠即可)

- 复现文件: D:\pair\inputs\reproduce.md   ← 单号、复现开始/结束时间、现象都从这里拿(每个单号一个「## 单号」小节)
- 根因文件: D:\pair\inputs\filter.md      ← 按单号找根因;小节里写了修复代码链接(commit / PR / diff)也会被认出来
- 源码树: D:\repo\opengauss-server
- 输出目录: D:\pair\out                   ← 下面按单号建子目录
- 间隔分钟: 5
- MCP配置:                                ← 可选,不给就继承 claude 配置目录里已配的 dbdog MCP
- 模型档:                                  ← 可选,CLAUDE_CONFIG_DIR
- 问题单文件:                              ← 可选,一行一个单号(+ 修复链接 + 事故窗),用来限定/覆盖复现文件里发现的单号
- 用例号正则:                              ← 可选,缺省认 DTS 单号 / OG-数字 / 大写字母-数字

复现文件小节长这样(标题含单号;开始/结束时间两行,或一行「复现时间: A ~ B」):

```markdown
## DTS2026090100123 OR-EXISTS 慢查询
复现开始时间: 2026-09-09 09:04:00
复现结束时间: 2026-09-09 09:07:00
现象: openGauss 业务库 bench 有一条涉及 t0、t1 的查询很慢……
```

根因文件小节(修复链接写在里面就会被拿去取 diff):

```markdown
## DTS2026090100123
根因: sublink pull-up 未做代价判断……
修复: https://codehub.example.com/r/openGauss/commit/abc123
```
