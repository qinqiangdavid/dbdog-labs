---
name: diag-pair
description: 正反两条诊断路径,各自独立产出 markdown。输入「反向」+ 正向诊断用的提示词 + 根因 + 代码路径,输出反向推断链路(该看什么、该提哪些假设、必需证据链)。输入「正向」+ hook 的 spans.jsonl 文件或含它的目录,输出正向假设图(假设↔假设、假设↔工具、收口)。触发词:diag-pair / 反向 / 正向 / 反推证据链 / 假设图。
---

# diag-pair —— 正、反两条路径,到产出为止

本 skill 只做两件事,做完即止:**不做对齐、不打分、不替你跑诊断**。两份 markdown 之后由你拿更强的模型做对比分析。

| 入口 | 你给什么 | 产出(markdown 为主,同名 .json 为结构化副本) |
|---|---|---|
| 反向 | 「反向」+ 正向诊断用的提示词(题面)+ 根因 + 代码路径(源码树目录) | `reverse-chain.md`:代码路径(核到 file:line)→ 日志 → 指标变化 → 现象 → 该提的假设方向与坑 → 必需证据链 |
| 正向 | 「正向」+ `spans.jsonl` 文件,或产生 span 的目录 | `forward-path.md`:假设树(父子关系)、每个假设名下的工具调用表(seq/时间/代理/工具/意图/状态)、假设出现顺序、收口边、未挂到假设的调用 |

按用户说的是「反向」还是「正向」分流,**不要混跑**。

## 环境

- 入口脚本 `scripts/run_pair.py`,只用 Python 3 标准库,Windows / macOS / Linux 通用。下文命令里的 `python` 在 mac/Linux 可能要写 `python3`。
- 正向:只要 Python。
- 反向:还要 Claude Code CLI(`claude` 在 PATH 里;Windows 装的是 `claude.cmd`,脚本会自己找)。模型档可用 `--config-dir`(即 `CLAUDE_CONFIG_DIR`)或 `--model` 指定;不给就用 claude 默认。分析会话会把 `DBDOG_OBS_REPORT_URL` 置空,不会往观测平台再长一棵树。
- 工具目录 `references/dbdog-tool-catalog.json`:反向证据链只准用这里的工具名(57 个 dbdog-mcp 工具 + `local_source_tree` 源码证据面)。要刷新用 `scripts/fetch-catalog.py`。

下文 `S` 指本 skill 的 `scripts` 目录:mac 常见 `~/.claude/skills/diag-pair/scripts`,Windows 是 `%USERPROFILE%\.claude\skills\diag-pair\scripts`。

## 1. 反向

用户给了「反向」+ 题面 + 根因 + 代码路径(正文、附件或文件路径都行;有 fix.diff 更好)。

先把题面存成一个文本文件(就是之后「诊断:」后面跟的那段),根因存成一个 markdown(若里面有 `## 现象量化` 小节会被抽进题面);代码路径是被测版本内核源码树的目录。然后:

```bash
python S/run_pair.py reverse --prompt 题面.txt --root-cause 根因.md --source 源码树目录 --out 输出目录
# 可选:--fix fix.diff  --config-dir <CLAUDE_CONFIG_DIR>  --model <模型名>  --force(已有产物也重跑)
# 题目目录形态(含 prompt.txt / ground-truth.md):python S/run_pair.py reverse 题目目录 --source 源码树目录
```

脚本做的事:把题面、根因、fix.diff、工具目录、源码树路径摆进一个临时工作目录,按 `prompts/reverse.md` 起一次 `claude -p`。反向角是出题人不是考生:**它会用 Grep/Read 真读源码树**把代码路径核到 file:line,但**不看任何 span、trace 或诊断轨迹**。跑完把 `reverse-chain.md`(和 `.json`)复制到 `--out`。

没有 `--source` 也能跑,但代码路径全是 `verified: false`,可信度降一档。耗时取决于模型,几分钟量级;失败看工作目录里的 `reverse.err`。

## 2. 正向

用户给了「正向」+ span 文件,或产生 span 的目录。零模型,纯格式化重构,秒出。

- 文件:一份 `spans.jsonl`(hook 每行一个 span),或 server 导出的 `{"spans":[...]}`
- 目录:里面有 `spans.jsonl` 就用它;也可以直接给 `~/.claude/dbdog-obs/`(整份日志,要用 `--trace` 或 `--session` 筛出那一次诊断)

```bash
python S/run_pair.py forward 路径/spans.jsonl
python S/run_pair.py forward 某次输出目录
python S/run_pair.py forward ~/.claude/dbdog-obs/spans.jsonl --trace <trace_id> --out 输出目录
```

产物默认写在输入旁(目录形态写在该目录里):`forward-path.md` + `forward-path.json`。

图从 span 已有字段长:`tags.hypothesis_id` / `parent_hypothesis_id` 优先,否则解析 intent 的 `[H2<H1] 类型=…; 假设=…; 判据=…; 关=…; 意图=…`。写在正文里的「提出 [H2] 类型=…; 假设=…」也采:扫 llm/agent span 的正文(本地 spans.jsonl 的全量字段 `output_local` / `thinking_local` 优先,server 导出只有截断后的 `output`),规则与语料仓 build-hypotheses.py 同一套(复述约定与示例行跳过、最早一次为准)。三类东西会被如实标出来、不编造:

- **未声明的假设**:只在 `[H2.1<H2]` 或 `关=` 里被引用、从没有调用以 `[H2]` 开头。正文里有「提出」行的会标「提出于正文」并带上假设文本;正文里也没有的才是真空节点
- **intent 写了字段但没 `[H..]` 头**:被测 agent 没守约定,单独列出并附原文
- **不带 intent 的调用**:Bash/Read/Agent 派发等本地工具,按工具名计数

## 3. 和手动诊断的关系

| 谁 | 干什么 |
|---|---|
| 你 | hook 装好后发 `诊断:`(或 `diag:`)+ 题面,最耗时;span 落在 `~/.claude/dbdog-obs/spans.jsonl`(或 `DBDOG_OBS_SPANS`) |
| 反向入口 | 题面 + 根因 + 代码路径 → 该走的路(`reverse-chain.md`) |
| 正向入口 | span → 实际走的路(`forward-path.md`) |
| 你 + 更强的模型 | 拿两份 markdown 做对比,找系统可优化点(不在本 skill 内) |

## 文件

```
SKILL.md
prompts/reverse.md                     反向角提示词
references/dbdog-tool-catalog.json     工具目录快照
scripts/run_pair.py                    入口(reverse / forward)
scripts/from_spans.py                  span → 假设图(被 run_pair 调用,也可单独跑)
scripts/fetch-catalog.py               刷新工具目录
scripts/*.test.py                      python scripts/from_spans.test.py / run_pair.test.py
```

## Windows 安装

把整个 `diag-pair` 文件夹放到 `%USERPROFILE%\.claude\skills\diag-pair\`(与 CLAUDE_CONFIG_DIR 对应的 `skills` 目录),重开 Claude Code 即可。需要 Python 3.8+ 在 PATH;反向另需 `claude` CLI。自检:

```bat
python %USERPROFILE%\.claude\skills\diag-pair\scripts\from_spans.test.py
python %USERPROFILE%\.claude\skills\diag-pair\scripts\run_pair.test.py
```
