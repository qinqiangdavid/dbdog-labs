---
name: span-graph
description: hook span 转假设图/树。输入 dbdog-obs hook 产出的 spans.jsonl 文件、server 导出的 span JSON,或含 spans.jsonl 的目录,输出正向假设图 markdown(假设↔假设父子、假设↔工具调用、收口、出现顺序、未挂到假设的调用)。零模型,秒出。触发词:span-graph / 正向 / 假设图 / span 转图 / span 转树。
---

# span-graph —— 从 hook span 重构诊断实际走的路

零模型、纯格式化重构:读 span 上已有的假设标注,长出「实际走的路」。产物是一份 markdown(附同名 .json),之后由人拿它与 evidence-chain 的产物做对比;本 skill 到产出为止,不做对比、不调模型、不重跑诊断。

## 输入

- 文件:一份 `spans.jsonl`(hook 每行一个 span),或 server 导出的 `{"spans":[...]}` / JSON 数组
- 目录:里面有 `spans.jsonl` 就用它;也可以直接给 `~/.claude/dbdog-obs/`(整份日志,用 `--trace` 或 `--session` 筛出那一次诊断)

span 来自 dbdog-obs hook:装好 hook 后发「诊断:」+ 题面,跑完 span 落在 `~/.claude/dbdog-obs/spans.jsonl`(Windows 是 `%USERPROFILE%\.claude\dbdog-obs\spans.jsonl`,或 `DBDOG_OBS_SPANS` 指定的位置)。

## 用法

`S` 指本 skill 的 `scripts` 目录;`python` 在 mac/Linux 可能要写 `python3`。

```bash
python S/from_spans.py 路径/spans.jsonl
python S/from_spans.py 某次输出目录
python S/from_spans.py ~/.claude/dbdog-obs/spans.jsonl --trace <trace_id> --out 输出目录
```

产物默认写在输入旁(目录形态写在该目录里):`forward-path.md` + `forward-path.json`。

## 图怎么长

`tags.hypothesis_id` / `parent_hypothesis_id` 优先,否则解析 intent 的 `[H2<H1] 类型=…; 假设=…; 判据=…; 关=…; 意图=…`(与 hook 的 hypothesis.mjs、语料仓的 build-hypotheses.py 同一套规则)。markdown 里有:

- 假设树:缩进 = 父子;每个假设下面一张表,列出该假设名下的工具调用(seq 是整条 trace 的全局序号、时间、主会话或哪个子代理、工具、意图、状态)
- 假设出现顺序
- 假设收口(`关=` 谁在第几步关了谁、判成什么)
- 未挂到假设的调用,分三类如实标出、不编造:**未声明的假设**(只被 `[H2.1<H2]` 或 `关=` 引用、没有调用以 `[H2]` 开头,通常是「提出 [H2]」写在正文里,hook 采不到);**intent 写了字段但没 `[H..]` 头**(agent 没守约定,附原文);**不带 intent 的本地工具**(Bash/Read/Agent 派发等,按工具名计数)

## 环境

- 只要 Python 3.8+(标准库),Windows / macOS / Linux 通用
- 自检:`python S/from_spans.test.py`

## 文件

```
SKILL.md
scripts/from_spans.py        入口
scripts/from_spans.test.py   测试
```
