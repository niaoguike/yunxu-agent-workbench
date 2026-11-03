# 确定长期指令与项目记忆的读写机制

Type: grilling
Mode: HITL
Labels: wayfinder:grilling
Status: open
Blocked by: 03, 05
Parent: [云端 Agent 工作台技术决策地图](../map.md)

## Question

账户长期指令、项目规则、自主记忆索引、分类条目和任务上下文怎样组织、注入和检索？怎样追踪来源、版本、去重、并发更新和删除生效？模型更换、账户切换、记忆关闭及已加载上下文的边界如何实现？是否需要语义检索，依据是什么？

## Completion criterion

所有权、优先级、读写时机与可见变更明确；区分长期信息、执行进度和历史对话；选择最小可验证的存储与检索方案。

## Dependencies

- [选择执行内核与适配边界](03-execution-kernel.md)
- [确定工作空间与并行修改的归属](05-workspace-and-write-coordination.md)

## Comments

地图建立阶段创建；未提供 Answer，不代表已有架构决定。

