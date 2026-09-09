---
name: diag-compare
description: 正反两条路对比,判问题在哪一层并落成 dbdog 改进清单。输入同一单号目录里 span-graph 的 forward-path 与 evidence-chain 的 evidence-chain(两份 markdown + json),先零模型预匹配,再用强模型把反向每条证据判成六类(无工具 / 应有结果但没有 / 结果不对 / 假设没提到 / 工具没调或调错 / 调对了但推理错),给出第一步走错在 seq 几、段对段差异,最后按 采集面/工具契约/数据正确性/skill方法论 列改进;批次跨单号聚合成 improvements.md。触发词:diag-compare / 对比 / 正反对比 / 第三块 / 找差距 / dbdog 改进清单。
---

# diag-compare —— 拿正反两条路找 dbdog 该改什么

前两个 skill 各出一份产物放在同一个单号目录:span-graph 的 `forward-path.md`(agent 实际走的路)和 evidence-chain 的 `evidence-chain.md`(该走的路 + 真取到的证据)。本 skill 拿这两份判:**问题到底出在 dbdog(工具面 / 采集面 / 数据面)还是 agent(假设 / 工具选择 / 推理),还是题本身**,最终落成可跨单号聚合的改进清单。

## 六类结论

| 结论 | 判据 | 归属 |
|---|---|---|
| 无工具 | 反向 outcome=no_tool | dbdog 工具面缺口 |
| 应有结果但没有 | 反向 outcome=empty_or_error | dbdog 采集面 / 接口缺陷,与 agent 无关 |
| 结果不对 | 反向 outcome=obtained_mismatch | dbdog 数据面缺陷,与 agent 无关 |
| 假设没提到 | 反向能取到,正向没有假设指向这条证据 | agent 假设层 |
| 工具没调或调错 | 有假设,但没调该用的工具 / 调错 | agent 工具层 |
| 调对了但推理错 | 调了、返回对,结论没用上或用反 | agent 推理层 |

前三类只看反向就能定,脚本预匹配阶段直接标;后三类要对照正向图,交给模型。「加分」级证据没调但根因已定死的,记「未调无碍」,不算问题。

## 输入(同一个单号目录)

- `evidence-chain.md` + `.json`(evidence-chain 出)
- `forward-path.md` + `.json`(span-graph 出)
- `forward-conclusion.md`(可选,span-graph 顺手落的 agent 最终回答;有它才能段对段比五段式)

## 用法

`S` 指本 skill 的 `scripts` 目录。**对比要用强模型**,`--config-dir` 或 `--model` 指过去。

```bash
python S/run.py D:\pair\out\DTS2026090100123 --config-dir <强模型的 CLAUDE_CONFIG_DIR>     # 单个单号 → compare.md / compare.json
python S/batch.py D:\pair\out --config-dir <…>                                            # 扫父目录,两份产物齐全的单号都比,再聚合 improvements.md
python S/batch.py D:\pair\out --aggregate-only                                            # 不跑模型,只重新聚合已有的 compare.json
```

脚本先跑 `prematch.py`(零模型)把反向每条证据按工具名对到正向调用上、给初判,写 `prematch.json`;再起一次 `claude -p`,按 `prompts/compare.md` 出六节:总判 / 证据矩阵 / 假设对照(含第一步走错 seq)/ 段对段 / dbdog 改进清单 / 题本身。产物 `compare.md` + `compare.json`,日志在 `work-compare\`。

## 产物

- 单号目录:`compare.md`(给人看)、`compare.json`(改进条目结构化:category / title / tool / evidence / fix / impact)
- 父目录:`improvements.md` + `.json`——跨单号聚合,同一条缺口按命中单号数排序,再按四类分组;另附各单号总判表(agent 定没定住根因、反向 verdict、主要在哪层、第一步走错 seq、六类计数)

改进条目的 title 要求稳定、不带题号,这样「schema 采集未开启」「recommendations 对 openGauss 零产出」这种在多个单号里都会撞到的缺口才能聚到一起——这就是给 dbdog 排优先级用的。

## 用户在会话里怎么说,你(Claude)怎么接

- 「对比 D:\pair\out\DTS…123」→ 跑 `run.py` 那个目录,贴 compare.md 的「一、总判」和「五、dbdog 改进清单」。
- 「把 D:\pair\out 里跑完的都对比一下,给我改进清单」→ 跑 `batch.py`(放后台,每个单号几分钟),完了贴 `improvements.md` 的排序表。
- 缺哪份产物就说清楚:没有 forward-path 去跑 span-graph,没有 evidence-chain 去跑 evidence-chain,不要自己拿两份 md 手工比。

## 文件

```
SKILL.md
prompts/compare.md          评审角提示词(六类结论 + 六节产物)
scripts/prematch.py         零模型预匹配(证据 ↔ 正向调用)
scripts/run.py              单个单号
scripts/batch.py            扫父目录 + 跨单号聚合
scripts/*.test.py           测试
```
