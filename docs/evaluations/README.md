# 端到端验收与对照评测

三层证据独立报告，不能把离线夹具的分数写成真实模型成功率。

| 层级 | 执行内容 | 独立验收 |
| --- | --- | --- |
| 真实任务 | CSV 清洗、计费修复、离线证据核验；单 Agent 与一个持久协作者对照 | 精确结果、19 项隐藏计费边界、引用覆盖、源材料未改动、策略遵循 |
| 循环策略 | 10 条固定工具轨迹、每条两次；固定8次中断 vs 真实辅助模型复核 | 永久失败检出、非循环误停、停止动作数、复核耗时与 Token |
| 可靠性 | 10 组每组20次回执丢失重试、重启、载荷冲突与文件合并 | 重复逻辑指派、回执一致性、并发冲突拒绝、重复应用幂等 |

## 复现

使用项目已有 Python 环境。真实任务和辅助模型复核会调用模型，需要在本地显式配置 trusted-local、有效 Codex 登录和必要网络代理。不要把凭据写入命令或提交结果。每次选择一个新的输出目录，脚本拒绝覆盖旧记录。

```powershell
# 不调用模型的控制面故障注入
.venv/Scripts/python.exe -m evaluations.reliability --output .data/evaluations/reliability-new

# 真实模型策略复核；先运行完，再进行真实任务对照，避免主动制造并发干扰
.venv/Scripts/python.exe -m evaluations.loops --repeats 2 --output .data/evaluations/loops-new

# 3任务 × 2策略 × 2轮；串行运行、每轮交替策略顺序
.venv/Scripts/python.exe -m evaluations.live --repeats 2 --timeout 300 --output .data/evaluations/acceptance-new

# 仅在整轮完成后导出报告；不复制数据库、用户会话或原始对话
.venv/Scripts/python.exe -m evaluations.report --live .data/evaluations/acceptance-new --loops .data/evaluations/loops-new --reliability .data/evaluations/reliability-new --output docs/evaluations/new-run
```

真实执行在独立数据库和全新项目中进行，不使用主工作台项目的任务、资料或记忆。系统默认引擎配置仍会影响结果，必须核对输出中的实际 model 和 native_version。单 Agent 禁止平台成员和原生子 Agent；协作组要求恰好一名参与执行的持久成员，允许修复重派，原生子 Agent 不参与对照。每个组的消耗包含主任务和协作者。

运行记录逐条追加 results.jsonl，失败和超时不丢弃，不自动重跑来替换分数。需要再次评测时保留旧目录，并报告两轮差异。小样本仅用于工程验收，不能写作公开任务泛化能力。

## 计量口径

- 交付成功必须同时满足平台 delivered、未超时、独立检查全部通过。Agent 自报的 delivery.json 不作为外部正确性证明。
- Token 来自原生 tokenUsage.total，按原生会话取最大累计值，避免逐事件或续跑重复累计；包含缓存输入、输出及辅助复核，不把 reasoningOutputTokens 再加到 totalTokens。
- 未获得实际账单，usd_cost 保持 null；不能将 Token 比例直接换算为付费成本比例。缺失使用量时标记为空，不补成0。
- 单轮耗时从提交消息开始到终态或截止；包含进程启动和编排。失败截止时长不代表成功交付时长。没有吞吐或并发压测。
- 循环轨迹是固定输入的受控对照，真实模型仅负责复核。静态基线不读取意图，复核读取任务意图及同一失败证据；这是两种策略的设计差异。
- 重试基线是相同逻辑请求每次使用新 ID，稳定 ID 策略复用 ID。调度出口不执行模型，避免把故障注入当作真实模型执行量。

## 验收器的边界

计费代码在独立子进程中运行隐藏检查，10秒超时；这不是不可信代码的强安全隔离。仅用于本机授权的固定测试任务。CSV 采用精确 JSON 比较；资料核验检查结论、数值和必要引用覆盖，不自动证明自然语言每句话都被引用支持。

## 离线复核已归档成果

不调用模型即可重跑已归档代码/JSON的语义检查，并比对运行时摘要：

```powershell
.venv/Scripts/python.exe -m evaluations.verify docs/evaluations/2026-11-06/results.json
```

此命令核对语义判分是否与记录一致，失败样本也应复现失败。它不重新证明历史平台状态、原材料未被修改或私有检查日志真实性；完整运行仍需上述真实模型实验。计费成果会在本机子进程执行，仅用于本仓库固定任务的授权成果，不用于任意不可信归档。

归档成果通过 .gitattributes 保留原始字节，避免 Git 换行转换破坏运行时 SHA-256 对照。历史基线执行代码为 bda5bd3；运行环境和成员检查范围修复为 b95273c。当前代码重跑默认采用修复后的环境。

判分更正 1.0.1：提示要求只用一名持久成员，并未禁止向同一成员再次指派修复。初版将指派条数误作成员数；更正为参与指派的成员去重数。原始 results.jsonl 不改写，导出记录保留 scoring_correction（如发生），主实验12组分数不变。
## 工具调用准确率与回复质量 Judge

在真实任务命令后添加 `--judge`，例如：

```powershell
.venv/Scripts/python.exe -m evaluations.live --repeats 2 --timeout 300 --judge --output .data/evaluations/judged-new
```

每轮任务结束后，在独立工作目录中调用禁止工具的辅助模型，使用独立 JSON Schema。工具准确率由模型判断工具选择和参数是否符合任务意图，允许等价路径及合理的失败探测，不等同于工具执行成功率。按 task/run/item 合并状态事件；只评有参数及结束状态的调用，每轮最多50次，报告总数、可评数、实评数和覆盖率。旧事件缺少参数时不猜测。该指标属于模型估计，尚需人工标注校准。

最终回复按 correctness、completeness、grounding、clarity 四维给出1–5分和理由，结合独立成果检查与交付文件判断。输入去除常见凭据；超过120000字符拒绝评分，避免静默截断。模型输出验证调用ID完整性、重复项、类型及分数范围。超时、无效输出或工具调用记为评分失败，分数为空，不补0。Judge延迟和usage单独保存，不混入任务端到端耗时或任务Token。

`results.jsonl` 和逐轮Trace保存 judge 证据；报告聚合实评调用准确率、完成/请求评分轮数及各质量维度均值。未启用时不调用评分模型。评分不改变原有 passed 判定。模型评分可能存在偏差，不能替代外部验收和人工抽检。启用此参数会新增真实模型调用。
