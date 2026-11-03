# 确定持久协作者与临时子 Agent 的调度

Type: grilling
Mode: HITL
Labels: wayfinder:grilling
Status: open
Blocked by: 03, 04, 05
Parent: [云端 Agent 工作台技术决策地图](../map.md)

## Question

主 Agent、持久成员和临时子 Agent 的身份、会话、指派与汇报如何持久化？排队/立即纠正、权限配置、预算汇总、停止全部成员和重启恢复怎样协调？如何保证有真实派发回执，且并行结果不丢失或静默覆盖？

## Completion criterion

持久成员可以对应不同执行器的独立原生会话；临时子 Agent 可能由执行器原生机制承载，必须按实际执行渠道标识。成员恢复、停止、模型选择与权限分别遵守适配器能力，不能从一个引擎的成功推出另一个兼容。

三种角色的生命周期与消息契约明确；可验证独立权限、稳定身份、恢复去重和文件应用规则，不以 Prompt 代替调度保证。

## Dependencies

- [选择执行内核与适配边界](03-execution-kernel.md)
- [确定双模式状态与人工确认的一致性规则](04-state-and-human-gates.md)
- [确定工作空间与并行修改的归属](05-workspace-and-write-coordination.md)

## Comments

地图建立阶段创建；未提供 Answer，不代表已有架构决定。

