# 确定双模式状态与人工确认的一致性规则

Type: grilling
Mode: HITL
Labels: wayfinder:grilling
Status: open
Blocked by: 03
Parent: [云端 Agent 工作台技术决策地图](../map.md)

## Question

任务、执行轮次、流程阶段、消息和审批怎样分别记录，谁是事实来源？如何绑定需求/成果版本与人工确认，处理重复答复、过期批准、模式切换、停止和崩溃恢复？框架检查点与业务记录之间如何协调，哪些操作可重试、哪些结果不明必须核对？

## Completion criterion

当前前提是复用执行器 loop。需分别处理平台 task/run、原生 session/thread/turn 和进程标识；原生轮次完成不直接推进业务阶段，恢复不得把不同执行器的原生标识混用。

关键状态及合法转换、确认失效和恢复语义完整；能推演重复消息、需求变更和批准竞态，不承诺任意副作用恰好一次。

## Dependencies

- [选择执行内核与适配边界](03-execution-kernel.md)

## Comments

地图建立阶段创建；未提供 Answer，不代表已有架构决定。

