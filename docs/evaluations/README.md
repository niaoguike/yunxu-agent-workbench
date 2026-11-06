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

真实执行在独立数据库和全新项目中进行，不使用主工作台项目的任务、资料或记忆。系统默认引擎配置仍会影响结果，必须核对输出中的实际 model 和 native_version。单 Agent 禁止平台成员和原生子 Agent；协作组要求恰好一个实质工作指派，原生子 Agent 不参与对照。每个组的消耗包含主任务和协作者。

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
