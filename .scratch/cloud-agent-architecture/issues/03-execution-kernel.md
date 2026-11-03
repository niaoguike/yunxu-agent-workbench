# 选择执行内核与适配边界

Type: grilling
Mode: HITL
Labels: wayfinder:grilling
Status: resolved
Claimed by: /root
Blocked by: 01
Parent: [云端 Agent 工作台技术决策地图](../map.md)

## Question

自由模式和需求开发模式共用什么执行内核？模型调用、工具循环、长期会话、持久协作者、阶段控制分别由谁拥有？选择原生内核、现成 SDK 或引擎适配路线，并给出最小公开 Interface、可替换 Adapter、所需技术栈和明确不自建的部分。模型与框架能力须引用能力研究，最终选择由用户确认。

## Completion criterion

路线及拒绝替代方案的理由明确；职责和可测试 Interface 可描述；不把旧技术候选直接视为批准。

## Dependencies

- [核实执行内核候选的实际能力](01-runtime-capabilities.md)

## Comments

地图建立阶段创建；未提供 Answer，不代表已有架构决定。

先前原生循环提案已由用户的新决定取代，当前采用[执行器复用方案](../proposals/execution-harness.md)。

## Answer

2026-11-03 用户明确要求“跟cindy对齐”：平台使用 Python/FastAPI，复用 Claude Code 与 Codex 原生 Agent loop。Claude Code 通过 Agent SDK 接入，Codex 通过 App Server 的双向协议接入；平台维护项目、任务、双模式阶段、人工确认、权限、扩展、记忆、协作和验收。

前端继续采用 React/TypeScript。执行器适配层分别处理原生会话、模型能力、输入、工具与审批事件、停止及恢复；不重新实现其内层循环。具体版本、存储、队列和云端隔离继续由后续决策确定。LangGraph 只保留为外层阶段编排候选，是否需要由状态决策确定。

详细 Interface、能力差异、失败规则及验证方式见[当前方案](../proposals/execution-harness.md)和 [ADR-0002](../../../docs/adr/0002-reuse-agent-harnesses.md)。语言与执行路线是两个独立选择，Python 不意味着自建 loop；[原生循环提案](../proposals/execution-kernel.md)及 [Java 可行性核查](../research/java-runtime-feasibility.md)保留为历史背景。本决定不声称已完成真实执行器、恢复或云端隔离验证。

