# 反向取证工作区(Windows,全自动)

解压后目录:

```
pair\
├── run.cmd                ← 双击/计划任务入口:先自检,再按 batch.md 顺序跑全部用例;日志在 logs\
├── schedule.cmd           ← 注册成每天 02:00 的 Windows 计划任务(跑一次即可;删任务:schtasks /delete /tn evidence-chain-nightly /f)
├── install-skills.cmd     ← 可选:把 skills\ 下两个 skill 复制到 %USERPROFILE%\.claude\skills,在 Claude Code 会话里也能用「反向取证」「正向」触发
├── batch.md               ← 唯一要改的配置:五行路径 + 阶段
├── inputs\
│   ├── reproduce.md       ← 多用例复现文件(现象 + 复现时间),每个用例一个「## 单号」小节
│   ├── filter.md          ← 多用例根因文件,每个用例一个「## 单号」小节;这里有的用例才跑
│   └── fixes\             ← 有本地 diff 就放这
├── mcp.json               ← dbdog MCP 配置(从 mcp.example.json 改;诊断和反向都用它)
├── dts-headers.txt        ← 可选:问题单页面要登录时放 Cookie(从 dts-headers.example.txt 改;别外传)
├── skills\
│   ├── evidence-chain\    ← 反向取证 skill(batch.py 在 scripts\ 下)
│   └── span-graph\        ← span 转假设图 skill
└── out\                   ← 产物,一个用例一个目录(见下)
```

## 上手

1. 装好 Python 3.8+ 和 Claude Code CLI(`python`、`claude` 在 PATH)。
2. Claude Code 里装好 dbdog-obs hook(插件 `dbdog-agent-obs`),并在 `settings.json` 的 env 里配 `DBDOG_OBS_REPORT_URL` / `DBDOG_OBS_API_KEY`。没有 hook 就没有 span,正向图会是空的。
3. 把复现文件、根因文件放进 `inputs\`;改 `batch.md` 里的源码树路径和问题单地址模板;`mcp.example.json` 改成 `mcp.json` 填上地址和鉴权。
4. 跑 `run.cmd`。它先自检(Python、claude、MCP 连通、源码树、每个用例能否切到、复现时间抓到什么),红的按提示修;通过就顺序跑。
5. 看 `out\batch-summary.md`。

跑完想挂成每晚自动:跑一次 `schedule.cmd`。幂等:已跑完的用例跳过,batch.md 新加的用例(或 inputs 里新加的小节)下次自动被捡起来。

## 每个用例目录里有什么

```
out\<单号>\
├── prompt.txt / root-cause.md / window.txt   切分出来的题面、根因、事故窗
├── spans.jsonl                                诊断阶段:hook 落的 span
├── work-diag\                                 诊断会话的题面、权限、stdout、err
├── forward-path.md (+ .json)                  正向阶段:假设图
├── evidence-chain.md (+ .json)                反向阶段:五段式 + 讲不讲得通 + dbdog 侧发现
└── work\                                      反向推导角的工作目录(claude.err / case.md / ticket.txt / fix.diff)
```

之后做「对比」时,把 forward\forward-path.md 和 reverse\evidence-chain.md 一起交给强模型,结果放 compare\。

## 复现文件与根因文件的格式

按单号切小节,标题行含单号即可:

```markdown
## DTS2026090100123 慢查询
复现时间: 2026-09-09 09:04:00 ~ 2026-09-09 09:07:00
现象: openGauss 业务库 bench 里一条对 t0、t1 的查询很慢……
复现步骤: ……
```

复现时间那行带「复现时间 / 执行时间 / 时间窗」等字样,或一行里有两个时间戳,都能抓到;`run.cmd` 自检会逐用例打印抓到的时间,先核一眼。

## 出了问题看哪

- 自检不过:按红字提示;MCP 连不上先在 Claude Code 里确认 dbdog 工具能用。
- 某用例反向失败:`out\<单号>\reverse\work\claude.err`;诊断失败:`out\<单号>\forward\diag\diag.err`。
- 正向图全是「未挂到假设」:诊断题面没带假设书写约定,或 hook 没装;批次自动跑的诊断会自动带。
- 整批日志:`logs\run-<日期时间>.log`。
