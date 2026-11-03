# 确定工作台与后台交互的统一接口

Type: grilling
Mode: HITL
Labels: wayfinder:grilling
Status: open
Blocked by: 04, 08, 09, 10
Parent: [云端 Agent 工作台技术决策地图](../map.md)

## Question

工作台如何查询与订阅任务、阶段、确认、成员、工具与成果？确定最小命令/查询/事件 Interface、资源标识、幂等和断线补齐规则，区分接收、持久化、派发、运行和完成。界面怎样清楚表达双模式与人工参与？

## Completion criterion

关键用户路径可映射到稳定接口和事件；重连不靠流式文本猜测状态；是否需要 UI 原型由明确未解问题决定。

## Dependencies

- [确定双模式状态与人工确认的一致性规则](04-state-and-human-gates.md)
- [确定持久协作者与临时子 Agent 的调度](08-persistent-collaboration.md)
- [确定阶段 Skill 与模式切换的执行规则](09-stage-workflow-and-mode-switch.md)
- [确定成果验收与运行预览的生命周期](10-delivery-verification-preview.md)

## Comments

地图建立阶段创建；未提供 Answer，不代表已有架构决定。

