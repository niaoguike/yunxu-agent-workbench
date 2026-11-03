# 确定工具扩展与阶段 Skill 的运行契约

Type: grilling
Mode: HITL
Labels: wayfinder:grilling
Status: open
Blocked by: 03, 05
Parent: [云端 Agent 工作台技术决策地图](../map.md)

## Question

插件、MCP 连接、通用 Skill 和阶段 Skill 共用哪些注册与调用 Interface？安装、项目启停、版本快照、凭据、校验和撤权如何落到执行入口？工具取消和重试怎样表达，如何阻止未注册能力及扩展绕过业务范围？

## Completion criterion

需分别验证执行器原生工具与平台 MCP 的执行前约束、撤权和审批映射；不能只验证平台可控工具路径。阶段 Skill、长期指令和平台记忆按每个执行器的真实加载机制适配，能力不足时明确不可用。

类型和作用域分清；安装更新失败可恢复；撤权对后续调用立即有效；Skill 不能自行提高权限或伪造阶段通过。

## Dependencies

- [选择执行内核与适配边界](03-execution-kernel.md)
- [确定工作空间与并行修改的归属](05-workspace-and-write-coordination.md)

## Comments

地图建立阶段创建；未提供 Answer，不代表已有架构决定。

