# 确定阶段 Skill 与模式切换的执行规则

Type: grilling
Mode: HITL
Labels: wayfinder:grilling
Status: open
Blocked by: 04, 06
Parent: [云端 Agent 工作台技术决策地图](../map.md)

## Question

七阶段预置流程如何表达输入、产出、检查和三个默认人工确认点？替换 Skill、复用成果、预先批准、退回、需求变更、跳过可选步骤和模式切换如何绑定版本并影响后续阶段？是否需要可运行状态原型才能确认边界？

## Completion criterion

每阶段进入/退出条件和确认作用域可验证；自由模式不被强制串入完整流程；未通过前置条件不能进入实作；必要时只提出精确原型票。

## Dependencies

- [确定双模式状态与人工确认的一致性规则](04-state-and-human-gates.md)
- [确定工具扩展与阶段 Skill 的运行契约](06-extension-and-skill-trust.md)

## Comments

地图建立阶段创建；未提供 Answer，不代表已有架构决定。

