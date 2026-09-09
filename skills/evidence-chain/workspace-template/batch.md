# 反向取证批次(全自动:诊断 → 正向 → 反向)

- 现象文件: inputs\reproduce.md
- 根因文件: inputs\filter.md
- 源码树: D:\repo\opengauss-server
- 输出目录: out
- 问题单地址模板: https://dts.example.com/issue/{id}
- 问题单请求头文件:
- 用例号正则: DTS\d+
- 阶段: 诊断,正向,反向
- 间隔分钟: 5
- MCP配置: mcp.json
- 诊断MCP配置: mcp.json
- 模型档:
- 诊断模型档:
- 模型:

（下面的表可以不写:用例清单来自根因文件里的单号,事故窗从复现小节解析,修复按问题单模板拼。
  只有要覆盖某个用例的窗 / 修复来源 / 指定已有 span 文件时才列一行。）

| 用例 | 事故窗 | 修复 | span 文件 | 备注 |
|---|---|---|---|---|
