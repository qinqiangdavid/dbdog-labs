---
name: evidence-chain
description: 根因反向取证。输入事故窗(用例执行时间)+ 现象(正向诊断用的题面)+ 根因 + 修复代码(diff 或 PR 链接,可选)+ 源码树路径(可选),推导角带 dbdog MCP 工具:五段式结论里 What happened / The root cause / What to do to fix 已给定,推导角推出另外两段:Why that broke things(机制,源码核到 file:line)与 How do we know(证据,真去取、边取边推),再加一段「讲不讲得通」自检(机制加证据能否解释现象量级、修复是否真断掉路径),附录汇总 dbdog 侧发现。产物 markdown 与被测 agent 的五段式结论同构,便于段对段对比。不看任何诊断轨迹。触发词:evidence-chain / 反向 / 反推证据链 / 反向取证 / 工具缺口。
---

# evidence-chain —— 从根因反向取证

五段式结论(What happened / Why that broke things / How do we know / The root cause / What to do to fix)里,反向场景已经拿到了三段:现象、根因、修复方案。本 skill 让推导角推出缺的两段:**Why**,根因怎么一步步变成现象,代码路径核到 file:line、可观测量变成什么样;**How do we know**,能佐证这条机制的证据,并且**真的去取**:第一条证据的内容决定第二条往哪取,取不到就改道,每条落一个取证结果(拿到且符合 / 拿到但不符 / 空或报错 / 无工具)。附录汇总 dbdog 侧缺口。再加一段「讲不讲得通」:取完证据回头检验机制能否解释现象的数字、修复是否真的断掉那条路,证据与标准答案矛盾时以证据为准,这是抓「题本身有问题」的地方。产物是一份与 agent 结论同构的五段式 markdown(附同名 .json),之后由人拿它与正向假设图(span-graph 的产物)做段对段对比;本 skill 到产出为止,不做对比,也不推「该提什么假设」——那留给对比的模型。

推导角是出题人不是考生:它**读源码树、读修复 diff、调 dbdog 工具**,但**不看 span、trace 或任何诊断过程**。设计场景是内外网隔离:诊断和反向取证都在内网同步跑,推导角只需要内网的 dbdog 与源码树。

## 输入

| 项 | 必需 | 形态 |
|---|---|---|
| 事故窗 | 是 | `--window`:用例执行时间,原样交给推导角当查询窗(题目目录形态下从 `window.txt` 读) |
| 现象 | 是 | 文本文件,内容就是正向诊断时「诊断:」后面那段题面 |
| 根因 | 是 | markdown,标准答案;若含 `## 现象量化` 小节,里面的数字会一并给推导角。**要是纯答案**:夹带诊断过程或评测记录会被推导角读到,提示词里已要求忽略,但别依赖它 |
| 修复代码 | 否,但强烈建议 | `--fix`:本地 diff/patch 文件,或 GitHub / Gitee 的 PR、commit 链接(自动取 `.diff`)。有它可信度从 medium 升到 high |
| 源码树 | 否,但强烈建议 | `--source`:被测版本内核源码树目录。没有则代码路径全为 `verified: false` |

手里是改后的整文件而不是 diff 时先做一份:`git diff --no-index 原文件 新文件 > fix.diff`。

## 用法

`S` 指本 skill 的 `scripts` 目录(mac 常见 `~/.claude/skills/evidence-chain/scripts`,Windows 是 `%USERPROFILE%\.claude\skills\evidence-chain\scripts`);`python` 在 mac/Linux 可能要写 `python3`。

```bash
python S/run.py --phenomenon 题面.txt --root-cause 根因.md --window "2026-09-09 09:04–09:07 (UTC+8)" --fix fix.diff --source 源码树目录 --out 输出目录
python S/run.py --phenomenon 题面.txt --root-cause 根因.md --window "…" --fix https://gitee.com/opengauss/openGauss-server/pulls/1234 --source 源码树目录 --out 输出目录
python S/run.py 题目目录 --source 源码树目录      # 目录里有 prompt.txt / root-cause.md(没有则用 ground-truth.md)/ window.txt(/ fix.diff)
# 可选:--mcp-config mcp.json 显式挂 dbdog MCP(不给则继承 config dir 里已配的);--config-dir <CLAUDE_CONFIG_DIR> 或 --model <名> 选模型档;--force 已有产物也重跑
```

脚本把事故窗、现象、根因、diff、工具目录、源码树路径摆进临时工作目录,按 `prompts/chain.md` 起一次带 dbdog MCP 的 `claude -p`(只禁 Bash、联网、派子代理;Read/Grep/Write 与 dbdog 工具放行,工作目录是临时目录),跑完把 `evidence-chain.md` / `.json` 复制到 `--out`,并用 `scripts/check_chain.py` 对 JSON 副本做四条格式检查(只管一致性,不判内容对错):

- 标成「用 dbdog 取」的证据,工具字段必须是工具目录里真实存在的名字(如 `get_dbdog_database_explain_plans`),不能写「查一下计划」这类意译,否则后面对正向图时对不上
- 每条证据标来源,只能是三个值之一:dbdog 工具取的 / 本机源码树读的 / 目录里没工具能取的
- 每条证据标真去取之后的结果,只能是四个值之一:拿到且符合预期 / 拿到但不符 / 空或报错 / 无工具
- 结果落在后三种的证据都是 dbdog 侧有问题的信号,必须同时出现在「dbdog 侧发现」列表里,不能只在证据链里标一下

推导角要真调工具,耗时十分钟量级;失败看工作目录里的 `claude.err`。

**dbdog MCP 怎么挂**:推导角跑在 `claude -p` 里,会继承 `CLAUDE_CONFIG_DIR` 下已配置的 MCP;内网机器上诊断用的那份 dbdog MCP 配置就够。也可以 `--mcp-config` 显式给一份(格式同 Claude Code 的 mcp.json),这时只用它。

## 产物结构(evidence-chain.md,正文五段与 agent 的五段式结论同构)

| 段 | 来源 | 内容 |
|---|---|---|
| What happened | 给定 | 用例执行时间、现象原话、现象量化、版本、症状类 |
| Why that broke things | 推 | 代码路径核到 file:line、关键判断、有无开关,每条 `verified`;走通后哪些可观测量变成什么样、差多少量级;有无日志 |
| How do we know | 推并真取 | 按实际取证顺序,每条 E 写工具、入参(含窗)、预期、**实际拿到什么**、结果四选一(`obtained_match` / `obtained_mismatch` / `empty_or_error` / `no_tool`)、推论、下一条为什么是它 |
| The root cause | 给定 | 照抄根因 |
| What to do to fix | 给定 | diff 改了什么、为什么能断掉 Why 里那条路;无 diff 标 `fix_diff: absent` |
| 讲不讲得通 | 推 | 取完证据回头检验:现象量化的每个数字被哪几条证据解释到什么程度;修复改的位置在不在机制路径上、改完路是否真断、有无残留场景;结论 `holds` / `partial` / `contradicted`。证据与标准答案矛盾时以证据为准写出来,这是抓「题本身有问题」的地方 |
| 附录:dbdog 侧发现 | 汇总 | 无工具 / 空或报错 / 拿到但不符,各写缺了它是「定不死」还是「少一面互证」 |

How do we know 的顺序是「先想要什么证据,再想用什么取,再真取」,工具目录只约束命名,不约束该不该列。之后与正向图对比时,前三类结论(无工具 / 应有结果但没有 / 结果不对)只看 How do we know 和附录就能定,与 agent 无关;「假设提错」「工具调错」由对比的模型拿正向图对着 Why 和 How do we know 判。

## 工作区目录结构(同事照这个摆)

```
D:\pair\                       ← 工作区(各自一份或共享盘)
├── batch.md                   ← 唯一要手写的配置:复现文件 / 根因文件 / 源码树 / 输出目录(模板 batch.example.md)
├── inputs\
│   ├── reproduce.md           ← 多用例复现文件:单号、复现开始/结束时间、现象都从这里拿(每个单号一个「## 单号」小节)
│   ├── filter.md              ← 多用例根因文件:按单号找根因;小节里写了修复代码链接也会被认出来
│   ├── tickets.txt            ← 可选:一行一个单号(+ 修复链接 + 事故窗),用来限定/覆盖复现文件里发现的单号
│   └── fixes\                 ← 本地 diff 放这
├── mcp.json                   ← dbdog MCP 配置(可选,不给就继承 claude 自己的)
└── out\                       ← 输出父目录,下面按单号建子目录
    ├── batch-summary.md / .json
    └── <单号>\
        ├── prompt.txt / root-cause.md / window.txt   切分出来的现象、根因、事故窗
        ├── evidence-chain.md (+ .json)               反向产物
        └── work\                                     推导角工作目录(claude.err / case.md / ticket.txt / fix.diff)
```

skill 本身装在 `%USERPROFILE%\.claude\skills\evidence-chain\`(插件或 zip),工作区只放数据和产物。本 skill 只做反向;正向假设图由 span-graph 单独跑,把 `forward-path.md` 放进同一个单号目录即可,对比时两份 md 就在一起。

**同事上手三步**:① 复制 `batch.example.md` 成 `batch.md`,改四个路径;② 跑 `run-batch.cmd batch.md`(mac/Linux 用 `run-batch.sh`),它先自检——Python、`claude`、MCP 连通、文件在不在、每个单号能否切到、抓到的事故窗和修复链接——红的按提示修;③ 看 `out\batch-summary.md`。幂等:已有产物的单号跳过,新加的单号下次自动被捡起来;有单号失败退出码为 1,可挂 Windows 计划任务 / cron。

## 批量:一次给全量单号,顺序跑

写一份 `batch.md`(模板见 `batch.example.md`),四行必填:复现文件、根因文件、源码树、输出目录;可选:间隔分钟(缺省 5)、MCP配置、模型档、模型、问题单文件、用例号正则。runner 自己组装每个单号:

- **单号清单**:复现文件标题行里出现的单号(缺省认 DTS 单号 / OG-数字 / 大写字母-数字,格式特别用「用例号正则」);给了「问题单文件」就以它为清单
- **现象**:按单号切复现文件的小节(优先「标题行含单号」,没有标题就从含单号的那行取到下一个单号之前),小节全文当题面
- **事故窗**:复现小节里的「复现开始时间 / 复现结束时间」两行拼成「开始 ~ 结束」;没有就认带时间关键词的一行,或一行里有两个时间戳的行;问题单文件里给了就用给的
- **根因**:按单号切根因文件的小节;没有的单号跳过并写进汇总
- **修复代码**:根因小节或复现小节里的 commit / PR / MR / diff 链接;GitHub、Gitee 的自动取 `.diff`,其它网页抓下来存 `ticket.txt` 并顺着页面里的代码链接取 diff;要登录的页面不指望,抓不到就把地址交给推导角;都没有按 `fix_diff: absent`

```bash
python S/batch.py batch.md --check       # 自检:环境 + 文件 + 每个单号切到什么、窗和修复抓到什么;不跑
python S/batch.py batch.md --dry-run     # 只切好每个单号的输入(out\<单号>\prompt.txt / root-cause.md / window.txt)、写汇总,不起模型
python S/batch.py batch.md               # 顺序跑,单号之间歇「间隔分钟」;已有产物的单号跳过
python S/batch.py batch.md --only DTS001,DTS002 --force
```

产物:`out\<单号>\evidence-chain.md`(+ `.json`;推导角日志在 `work\`),以及 `out\batch-summary.md` / `.json`,一行一个单号:状态、讲不讲得通的结论、四类取证计数、dbdog 侧发现条数。

## 环境

- Python 3.8+(标准库)+ Claude Code CLI(`claude` 在 PATH;Windows 的 `claude.cmd` 会自动找到)+ 可用的 dbdog MCP
- 分析会话会把 `DBDOG_OBS_REPORT_URL` 置空,不会往观测平台再长一棵树
- 工具目录 `references/dbdog-tool-catalog.json`(57 个 dbdog-mcp 工具 + `local_source_tree` 源码证据面);刷新用 `scripts/fetch-catalog.py`
- 自检:`python S/run.test.py` 与 `python S/batch.test.py`

## 文件

```
SKILL.md
prompts/chain.md                       推导角提示词
references/dbdog-tool-catalog.json     工具目录快照
scripts/run.py                         单用例入口
scripts/batch.py                       批量入口(--check 自检 / --dry-run 切分 / 顺序跑,写 batch-summary.md+.json)
batch.example.md                       批次文件模板
run-batch.cmd / run-batch.sh           一键:先自检再跑
scripts/check_chain.py                 产物校验(也可单独跑:python check_chain.py evidence-chain.json;打印四类取证结果计数)
scripts/fetch-catalog.py               刷新工具目录
scripts/run.test.py / batch.test.py    测试
workspace-template/                    Windows 工作区模板(run.cmd 自检→跑 / schedule.cmd 计划任务 / batch.md / inputs / README)
```
