---
status: superseded
superseded_by: 0002-reuse-agent-harnesses.md
date: 2026-11-03
---

# 使用 Python 原生 Agent 循环与 LangGraph 编排

本决策已被 [ADR-0002](0002-reuse-agent-harnesses.md) 取代。Python 语言选择保留，当前执行路线改为复用成熟执行器的 Agent loop；下文只记录当时取舍。

平台需要统一控制自由模式、需求开发阶段、人工确认和持久协作者，因此采用 Python 实现原生 Agent 工具循环，并使用 LangGraph 的图编排与检查点能力，模型通信复用供应商客户端 SDK。取舍是承担执行循环、上下文、事件与取消传播的工程工作，以保留对业务语义的直接控制；数据库、队列和云端隔离路线并未随之确定。

决策、Interface 和替代方案依据见[选择执行内核与适配边界](../../.scratch/cloud-agent-architecture/issues/03-execution-kernel.md)。
