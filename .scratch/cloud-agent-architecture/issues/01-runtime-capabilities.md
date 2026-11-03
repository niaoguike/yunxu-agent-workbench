# 核实执行内核候选的实际能力

Type: research
Mode: AFK
Labels: wayfinder:research
Status: resolved
Claimed by: /root/runtime_research
Blocked by: None
Parent: [云端 Agent 工作台技术决策地图](../map.md)

## Question

哪些执行内核路线真正支持本项目的工具循环、结构化输出、人工中断、取消、恢复、独立协作者与模型接入？比较原生工具循环配合状态图、现成 Agent SDK、既有引擎适配三类路线的职责、限制和成本来源；使用官方文档与已有源码，不以功能名证明恰好一次或完整恢复。产出能力矩阵、证据、无法确认项和建议，保留最终路线选择给用户。

## Completion criterion

每项能力有一手来源；区分框架提供与平台必须自建；给出足以选择路线的权衡。

## Dependencies

无，可直接开展。

## Comments

地图建立阶段创建；未提供 Answer，不代表已有架构决定。


## Answer

三路线均可承接工具循环，但都不自动保证工具恰好一次、云端权限隔离或完整故障恢复。原生循环配状态图控制面最明确；Claude Agent SDK 降低循环与工具实现成本但模型与宿主边界需验证；Cindy 式适配已有持久协作与队列静态证据，云端改造和故障语义仍由平台承担。选型留待用户。

[能力矩阵、官方来源、未知及验证建议](../research/runtime-capabilities.md)。

