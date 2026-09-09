你是数据库内核与可观测性的资深工程师。下面给你一道 openGauss 诊断题的**根因**(标准答案),
你的任务是**反向推导**:根因成立,在监控里应该看到什么、该用哪些工具去拿、拿到的返回里看哪一段。
你**没有**这道题的任何诊断轨迹,不要假设别人怎么查过;只从根因、修复代码和工具目录出发。

## 输入文件(都在当前目录)
- `case.md`:题面(喂给被测 agent 的原话)、现象量化、版本、症状类
- `root-cause.md`:根因说明(标准答案原文)
- `fix.diff`:修复代码 diff(可能不存在——不存在就在结论里标 `fix_diff: absent`,推导可信度降一档)
- `tool-catalog.json`:dbdog-mcp 的工具目录(名称、描述、参数名与说明)。**证据链只能用目录里的工具**
- `source-tree.txt`:被测版本内核源码树的本机路径(一行;**可能不存在**——不存在就不核源码,每条代码路径 `verified: false` 并注明"无源码树")。**你可以、也应该用 Read / Grep / Glob 直接读这棵源码树**——
  你是出题人,不是跑诊断的 agent,读源码不会污染任何东西。目的是把"代码路径"从猜测变成核实:文件、行号、函数名、判断条件、有无开关。

## 推导顺序(按这个顺序写,不要跳)
1. **代码路径**:根因触发的执行路径是什么。**先到源码树里核**:用 Grep 找关键函数/GUC/错误文本,用 Read 看那几行,
   写出 `file:line` 与函数名、关键判断条件(例如"只按形状转换,没有代价比较分支")、有没有能关掉它的开关(GUC 定义处也要核)。
   每条标 `verified: true|false`(true = 你真的在源码里读到了;没找到就写 false 并说搜了什么)。有 fix.diff 就先读 diff 再去源码定位。
2. **日志**:这条路径会不会写日志?写哪条(级别、大致文本)?没有就明说"无日志,只能靠指标/计划面"。
3. **指标与采集面变化**:这条路径走通时,哪些可观测量会变:执行计划形状、等待事件、行数/块数、单次耗时分布、锁、资源。每条写"变成什么样"和"和正常对比差多少量级"。
4. **可能现象**:用户会看到什么(症状类:慢 / 报错 / 结果错 / 资源);现象量化那几个数字要能用第 3 条解释。
5. **假设方向**:如果一个 agent 只看到现象、不知道根因,**该往哪几个方向提假设**才能走到根因。每个方向写:
   `id`(HD1、HD2…)、`type`(现象确认 / 根因)、`text`(假设)、`expect`(判据:看到什么算成立/证伪)、`why`(为什么这个方向能通向根因)。
   同时写 **容易走偏的方向**(`decoys`):看起来像、但会把人带到错误结论的假设,各一句为什么是坑。
6. **必需证据链**:根因要"定死",最少要哪几条证据。每条:
   `id`(E1、E2…)、`name`、`tier`(必需 / 加分)、`tool`(目录里的工具名)、`params`(关键入参形态)、
   `expect_in_output`(返回里看哪一段、什么值才算拿到;若 tool 是 local_source_tree,这里直接写核过的 file:line 与该看的条件)、`establishes`(确立什么)、`excludes`(排除什么)、
   `depends_on`(依赖哪条证据先拿到)、`inference`(拿到这条证据后**必须做的推论**,例如"t0 只有 10 行 → c0>0 几乎全真 → OR 本该短路,EXISTS 不该被执行";
   证据拿到而推论没做,对齐时要判「读了没用」,所以这一栏要写具体)。
   "必需"的标准:缺了它,根因就只能是猜测。加分 = 互证面。

## 输出
1. 把结构化结果写到 `./reverse-chain.json`,形如:
```json
{"case": "...", "fix_diff": "present|absent", "confidence": "high|medium|low", "confidence_why": "...",
 "source_tree": "...", "code_paths": [{"path": "file:line", "symbol": "...", "condition": "...", "guc": "...", "role": "...", "verified": true}],
 "logs": [{"level": "...", "text_like": "...", "when": "..."}],
 "observables": [{"surface": "plan|wait|rows|latency|lock|resource|log", "change": "...", "magnitude": "..."}],
 "phenomena": [{"symptom_class": "slowness|error|wrong_result|resource", "text": "..."}],
 "hypothesis_directions": [{"id": "HD1", "type": "confirm|cause", "text": "...", "expect": "...", "why": "..."}],
 "decoys": [{"text": "...", "why_trap": "..."}],
 "evidence_chain": [{"id": "E1", "name": "...", "tier": "must|bonus", "tool": "...", "params": "...", "expect_in_output": "...", "establishes": "...", "excludes": "...", "depends_on": []}]}
```
2. 再用中文把上面六段写成 `./reverse-chain.md`(**主产物**,后续会拿它与正向假设图做对比分析),每段一小节,不要复述 JSON;
   假设方向与证据链两段要把 id(HD1/E1…)、工具名、判据、inference 写全,别只留在 JSON 里。

要求:JSON 里所有字符串一律用双引号,字符串内的引号用中文引号「」或转义,不要用单引号包字符串(否则文件不合法);工具名必须是目录里的原名;不要写"查一下执行计划"这种没有工具和入参的话;量级要给数字或倍数;
不确定的地方写"不确定",不要编。
