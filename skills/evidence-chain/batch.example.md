# 反向取证批次(示例,复制后改路径;Windows 路径直接写反斜杠即可)

- 问题单文件: D:\pair\inputs\tickets.txt      ← 一行一个单号,后面可跟修复代码链接和事故窗(见下)
- 现象文件: D:\pair\inputs\reproduce.md       ← 多用例复现文件,按单号找小节;小节里带复现时间的行会被解析成事故窗
- 根因文件: D:\pair\inputs\filter.md          ← 多用例根因文件,按单号找小节;没有的单号跳过
- 源码树: D:\repo\opengauss-server
- 输出目录: D:\pair\out
- 阶段: 正向,反向                               ← 写 诊断,正向,反向 则连正向诊断一起自动跑
- 间隔分钟: 5
- MCP配置:                                     ← 可选,不给就继承 claude 配置目录里已配的 dbdog MCP
- 模型档:                                       ← 可选,CLAUDE_CONFIG_DIR

tickets.txt 的样子(链接、事故窗都可省;事故窗省了就从复现小节解析):

```
# 单号  修复代码链接(commit / PR / 本地 diff)  事故窗
DTS2026090100123  https://codehub.example.com/r/commit/abc123  2026-09-09 09:04–09:07 (UTC+8)
DTS2026090100456  https://gitee.com/opengauss/openGauss-server/pulls/8080
DTS2026090100789  D:\pair\inputs\fixes\789.diff
DTS2026090100999
```

不想维护 tickets.txt 也行:去掉「问题单文件」那行,就跑根因文件里出现的全部单号(可用「用例号正则」限定),修复来源可用「问题单地址模板: https://…/{id}」拼出问题单网页去找(页面要登录就别指望它)。
要覆盖某个单号的窗 / 修复 / 指定已有 span 文件,再在下面列一行:

| 用例 | 事故窗 | 修复 | span 文件 | 备注 |
|---|---|---|---|---|
