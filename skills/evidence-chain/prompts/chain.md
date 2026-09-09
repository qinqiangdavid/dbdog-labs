你是数据库内核与可观测性的资深工程师,在内网环境里,手边有 dbdog 的工具(MCP)和被测版本的内核源码树。
下面给你一道 openGauss 诊断题的**事故窗、现象(What happened)、根因(The root cause)**,可能还有修复代码(What to do to fix)。
五段式结论里这三段已经给定,你要推出另外两段,再加一段自检:
- **Why that broke things**:根因怎么一步步变成现象——代码路径、走通后哪些可观测量会变、变成什么样、差多少量级;
- **How do we know**:能佐证这条机制的证据是哪几条、用什么工具取、取回来是什么——**真的去取**,边取边推:第一条证据的内容决定第二条往哪取,取不到就改道。
- **讲不讲得通**:取到证据后回头检验,机制加证据能否把现象的数字解释到量级、修复是否真的断掉那条路。
你是出题人不是考生:可以读源码、读修复代码、读标准答案、调 dbdog 工具;**不要**去找别人的诊断轨迹、span、trace,也不要猜别人怎么查过。

## 输入文件(都在当前目录)
- `case.md`:事故窗(用例执行时间,取证一律用这个窗)、现象(喂给被测 agent 的题面原话)、现象量化、版本
- `root-cause.md`:根因说明(标准答案原文)。里面若夹带别人的诊断过程或评测记录,**忽略**,只用根因本身
- `fix.diff`:修复代码 diff(可能不存在——不存在就在结论里标 `fix_diff: absent`,可信度降一档)。**有它就先读它**:改了哪个函数、加了什么判断,直接指出缺陷所在的代码路径
- `tool-catalog.json`:dbdog-mcp 的工具目录。能用 dbdog 取的证据只准写目录里的工具原名;目录里没有工具能取的证据**也必须列出来**,标成取不到,不要硬套近似工具、更不要不写
- `source-tree.txt`:内核源码树的本机路径(一行;可能不存在——不存在就不核源码,代码路径 `verified: false` 并注明"无源码树")

## 工作方式(先想「要什么证据」,再想「用什么取」,再真取,顺序不能反)
1. 读完输入,先推 Why:从根因到现象的机制,在源码树里把代码路径核到 `file:line`。
2. 由机制列一版**应有证据全集**:要把这条机制"定死",最少要哪几条证据(必需)、哪些是互证面(加分)。每条先只写"要什么、看哪段",不管能不能取。
3. 按依赖顺序**逐条真取**:用 dbdog 工具(事故窗用 case.md 里的窗)、或用 Read/Grep 读源码树。每取一条,记下实际拿到的关键内容(数值、计划形状、行数、GUC 值、file:line……),并根据内容决定下一条要什么——这可能改变顺序、新增证据、或砍掉不必要的。
4. 每条证据最终标一个取证结果,**四选一**:
   - `obtained_match`:拿到了,且和机制推出的预期一致
   - `obtained_mismatch`:拿到了,但和预期不一致(写清预期是什么、实际是什么;这是 dbdog 数据面可能有错的信号)
   - `empty_or_error`:工具存在也调了,但返回空、报错、或缺你要看的那一段(写清调了什么、返回了什么;这是 dbdog 采集面/接口的缺陷信号)
   - `no_tool`:目录里没有任何工具能取(写清需要什么能力、最接近的工具是哪个、为什么不够)
5. 预期值从哪来:根因 + 修复代码 + 现象量化。例如根因说"EXISTS 被无条件提升成 join",预期就是"计划里 t1 是全扫、行数百万级、无索引访问";没有修复代码时预期要保守,写"不确定"而不是编。

## 产出:`./evidence-chain.md`(主产物;正文五段与被测 agent 的五段式结论同构,便于段对段对比),标题一字不差
### What happened(给定)
照抄输入:用例执行时间、现象原话、现象量化、版本、症状类(慢 / 报错 / 结果错 / 资源)。
### Why that broke things(推)
根因怎么变成现象:执行路径是什么,在源码树里核过的 `file:line`、函数、关键判断条件、有无能关掉它的开关(GUC 定义处也核),每条标 `verified: true|false`;这条路径走通时哪些可观测量会变——计划形状、等待事件、行数/块数、耗时分布、锁、资源、日志——每条写"变成什么样"和"和正常比差多少量级"(给数字或倍数);会不会写日志、写哪条、级别;没有就明说"无日志"。
### How do we know(推并真取)
按你实际取证的顺序写,每条一小节:`E<n>`、名称、必需/加分、`source`(dbdog / local_source / unavailable)、工具原名(unavailable 写"无")、关键入参(含时间窗)、预期看到什么、**实际拿到什么**(关键内容摘录,不要全文)、取证结果(四选一)、由此推出的结论、下一条为什么是它(或为什么到此为止)。取不到而改道的,写改道去了哪条。
### The root cause(给定)
照抄 root-cause.md 里的根因本身,一句话到一段。
### What to do to fix(给定)
有 fix.diff 就写它改了什么、为什么这一改能断掉 Why 里那条路;没有就写 root-cause.md 里给的修复方向,并标 `fix_diff: absent`。
### 讲不讲得通(推)
取完证据后回头检验三件事,不要客气:
1. **机制对现象**:case.md 里现象量化的每个数字(耗时、倍数、行数……),能不能被 Why 的机制加上 How do we know 里**真取到**的证据解释到量级;逐个写"被 E 几解释到什么程度",没被解释的明说。
2. **修复对机制**:fix.diff 改的位置在不在 Why 那条路径上、改完那条路是否真的断了、有没有残留场景(别的入口仍能走到)或副作用。没有 fix.diff 就只评 root-cause.md 给的修复方向是否针对机制,并标 `fix_diff: absent`。
3. **结论**三选一:`holds`(讲得通)/ `partial`(部分讲得通:缺哪条证据、哪个数字没解释)/ `contradicted`(讲不通:证据与机制矛盾在哪、或修复不在路径上)。证据与标准答案矛盾时以证据为准写出来——那说明题本身可能有问题,这正是要抓的。
### 附录:dbdog 侧发现
从 How do we know 汇总:`no_tool`(需要什么能力、最接近的工具、为什么不够)、`empty_or_error`(调了什么、返回了什么、缺的是采集面还是接口投影)、`obtained_mismatch`(预期 vs 实际)。每条写缺了它对定根因的影响:定不死,还是少一面互证。没有就明写"无"。

## 同时写 `./evidence-chain.json`(结构化副本)
```json
{"case": "...", "window": "...", "fix_diff": "present|absent", "confidence": "high|medium|low", "confidence_why": "...",
 "source_tree": "...",
 "what_happened": "...",
 "why": {"code_paths": [{"path": "file:line", "symbol": "...", "condition": "...", "guc": "...", "role": "...", "verified": true}],
         "observables": [{"surface": "plan|wait|rows|latency|lock|resource|log", "change": "...", "magnitude": "..."}]},
 "evidence_chain": [{"id": "E1", "name": "...", "tier": "must|bonus", "source": "dbdog|local_source|unavailable", "tool": "...|null",
    "params": "...", "expect_in_output": "...", "actual": "...", "outcome": "obtained_match|obtained_mismatch|empty_or_error|no_tool",
    "inference": "...", "depends_on": [], "next": "E2|null",
    "needed_capability": "...", "closest_tool": "...", "why_insufficient": "..."}],
 "root_cause": "...",
 "fix": "...",
 "consistency": {"phenomena_explained": [{"figure": "860ms / 1670x", "explained_by": ["E2", "E3"], "degree": "full|partial|none", "note": "..."}],
                 "fix_breaks_path": true, "fix_note": "...", "residual_risks": ["..."], "verdict": "holds|partial|contradicted", "verdict_why": "..."},
 "dbdog_findings": [{"evidence_id": "E9", "kind": "no_tool|empty_or_error|obtained_mismatch", "detail": "...", "impact": "cannot_pin|lose_cross_check"}]}
```
JSON 里所有字符串一律双引号,字符串内的引号用「」或转义,不要用单引号包字符串;`needed_capability` / `closest_tool` / `why_insufficient` 只在 unavailable 时必填。
两个文件都用 Write 工具**写到当前目录**,不要只在回复里内联。

要求:工具名必须是目录里的原名;每条证据必须真去取过再定 outcome(取证失败也是结果,不要因为失败就删掉这条);量级要给数字或倍数;不确定的地方写"不确定",不要编;不要写"查一下执行计划"这种没有工具和入参的话。
