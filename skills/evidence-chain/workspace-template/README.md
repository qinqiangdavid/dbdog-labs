# 反向取证工作区(Windows)

解压后目录:

```
pair\
├── run.cmd                ← 双击/计划任务入口:先自检,再按 batch.md 顺序跑全部单号;日志在 logs\
├── schedule.cmd           ← 注册成每天 02:00 的 Windows 计划任务(跑一次即可;删任务:schtasks /delete /tn evidence-chain-nightly /f)
├── install-skills.cmd     ← 可选:把 skills\ 下两个 skill 复制到 %USERPROFILE%\.claude\skills,在 Claude Code 会话里也能用「反向取证」「正向」触发
├── batch.md               ← 唯一要改的配置:复现文件 / 根因文件 / 源码树 / 输出目录
├── inputs\
│   ├── reproduce.md       ← 多用例复现文件:单号、复现开始/结束时间、现象都从这里拿,每个单号一个「## 单号」小节
│   ├── filter.md          ← 多用例根因文件:按单号找根因;小节里写了修复代码链接也会被认出来;这里没有的单号跳过
│   └── fixes\             ← 有本地 diff 就放这
├── mcp.json               ← dbdog MCP 配置(从 mcp.example.json 改)
├── skills\
│   ├── evidence-chain\    ← 反向取证 skill(batch.py 在 scripts\ 下)
│   └── span-graph\        ← span 转假设图 skill(单独跑,产物放进同一个单号目录)
└── out\                   ← 输出父目录,下面按单号建子目录(见下)
```

## 上手

1. 装好 Python 3.8+ 和 Claude Code CLI(`python`、`claude` 在 PATH),Claude Code 里 dbdog MCP 能用。
2. 把复现文件、根因文件放进 `inputs\`;改 `batch.md` 里的源码树路径;`mcp.example.json` 改成 `mcp.json` 填上地址和鉴权(不填就继承 claude 自己配的 MCP)。
3. 跑 `run.cmd`。它先自检(Python、claude、MCP 连通、文件、每个单号能否切到、抓到的事故窗和修复链接),红的按提示修;通过就顺序跑。
4. 看 `out\batch-summary.md`。

跑完想挂成每晚自动:跑一次 `schedule.cmd`。幂等:已跑完的单号跳过,复现文件里新加的小节下次自动被捡起来。

## 每个单号目录里有什么

```
out\<单号>\
├── prompt.txt / root-cause.md / window.txt   切分出来的现象、根因、事故窗
├── evidence-chain.md (+ .json)               五段式 + 讲不讲得通 + dbdog 侧发现
└── work\                                     推导角工作目录(claude.err / case.md / ticket.txt / fix.diff)
```

正向假设图用 span-graph 单独出,把 `forward-path.md` 放进同一个单号目录;之后做「对比」时两份 md 就在一起。

## 两个输入文件的格式

按单号切小节,标题行含单号即可。复现文件的小节里放复现开始/结束时间(或一行「复现时间: A ~ B」)和现象;根因文件的小节里放根因,有修复代码链接就写上:

```markdown
## DTS2026090100123 慢查询
复现开始时间: 2026-09-09 09:04:00
复现结束时间: 2026-09-09 09:07:00
现象: openGauss 业务库 bench 里一条对 t0、t1 的查询很慢……
```

```markdown
## DTS2026090100123
根因: ……
修复: https://codehub.example.com/r/openGauss/commit/abc123
```

`run.cmd` 自检会逐单号打印抓到的事故窗和修复链接,先核一眼。

## 出了问题看哪

- 自检不过:按红字提示;MCP 连不上先在 Claude Code 里确认 dbdog 工具能用。
- 某单号失败:`out\<单号>\work\claude.err`。
- 整批日志:`logs\run-<日期时间>.log`。
