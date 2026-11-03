---
status: accepted
date: 2026-11-03
supersedes: 0001-python-agent-runtime.md
---

# Python 平台复用成熟 Agent 执行器

用户明确要求执行方式与参考项目对齐，因此保留 Python/FastAPI 平台与 React/TypeScript 前端，复用 Claude Code 和 Codex 的 Agent loop：前者通过 Agent SDK 适配，后者通过 App Server 协议适配。平台拥有项目、任务、双模式阶段、人工确认、权限策略、记忆、协作与验收，执行器拥有内层模型决策、工具循环及原生会话；适配器负责两者的身份、事件和控制转换。

这替代了自建内层循环的路线，代价转为执行器版本兼容、能力差异、云端部署与认证隔离。LangGraph 是否用于外层业务阶段由状态设计后续决定，不再作为自建内层循环的必选依赖；具体存储、队列与隔离环境尚未定案。

详细契约见[执行器复用方案](../../.scratch/cloud-agent-architecture/proposals/execution-harness.md)，依据见[官方接口与源码核查](../research/执行器复用对齐.md)。
