# 确定完整方案的验证与评测方法

Type: grilling
Mode: HITL
Labels: wayfinder:grilling
Status: open
Blocked by: 03, 04, 07, 10, 11
Parent: [云端 Agent 工作台技术决策地图](../map.md)

## Question

49项产品验收如何映射至外部行为测试、故障注入和真实执行器评测？确定最高可测试 Interface、固定材料、开发/保留集、对照实验、成本与质量口径，以及何时足以确认方案可实现。明确哪些证明可用执行器事件夹具，哪些必须运行真实 SDK/CLI、原生工具和模型；分别验证 Claude Code、Codex，不能以替代模型循环证明其兼容。

## Completion criterion

验证覆盖双模式、版本/权限、协作、记忆、恢复和交付；不写虚构成功率；测试不镜像实现。

## Dependencies

- [选择执行内核与适配边界](03-execution-kernel.md)
- [确定双模式状态与人工确认的一致性规则](04-state-and-human-gates.md)
- [确定长期指令与项目记忆的读写机制](07-personalization-and-memory.md)
- [确定成果验收与运行预览的生命周期](10-delivery-verification-preview.md)
- [确定工作台与后台交互的统一接口](11-interfaces-and-workbench.md)

## Comments

地图建立阶段创建；未提供 Answer，不代表已有架构决定。

