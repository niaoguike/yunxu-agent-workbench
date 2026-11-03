# 执行内核能力核实

日期：2026-11-03。官方 API 文档读取 + 已核实 Cindy 固定提交源码研究；未安装运行，不作选型决定。本文件为本地来源快照，无分支或推送。

| 能力 | 原生工具循环 + 可选 LangGraph | Claude Agent SDK | Cindy 式引擎适配 |
|---|---|---|---|
| 工具循环 | 模型返回调用，应用执行/回传；图编排状态 [1][2] | SDK 提供循环、内置工具、自定义工具/MCP [4] | 适配已有执行器及队列消费；桌面 IPC 非云 API [8] |
| 多模型 | 平台适配各供应商 API 和工具历史格式 [1] | model/fallback_model/set_model 是实际接口；不能推导任意供应商兼容 [4] | Worker 解析 providerId/model/effort；供应商实际兼容仍需逐项验证 [9] |
| 结构化输出 | 模型工具 schema 不等于最终成果校验，平台另验领域规则 [1] | output_format 支持 json_schema，业务及成果校验仍由平台负责 [4] | 现有研究不足以确认统一最终输出 schema |
| 人工暂停 | interrupt + checkpointer + thread_id + Command(resume)，平台保存确认对象、审批者和版本 [2][3] | can_use_tool 和权限回调提供工具边界；平台另建持久阶段确认 [4][5] | 队列暂停和交互门禁已核实，阶段确认仍由平台定义 [8] |
| 停止 | 平台传播取消至请求、工具、子进程；暂停图不等于终止外部动作 [3，推论] | interrupt 仅 streaming；旧消息 buffer 必须 drain 至 ResultMessage；不能视为副作用回滚 [4] | stop 有 generation/token、abort 边界核对；默认可清队列，保留须 keepQueue [8] |
| 恢复 | 图状态 checkpoint 不等于外部工具幂等；需生产持久后端和稳定线程 ID [2][3] | resume 恢复会话，支持外部存储；平台恢复工作空间、凭据及确认状态，非整个容器恢复 [5][6] | 输入快照/clientId/恢复清洗已核实，非所有工具执行过程恢复 [8] |
| 独立协作者 | 独立图线程可作基础，平台保存身份、指派、汇报和消息 [2，设计推论] | 子 Agent 有独立能力，但不能由功能名证明本项目持久成员生命周期；需平台成员与会话映射 [4][7] | 数据库 lead/worker、独立 session、派发和唤醒已核实，云端生命周期仍需平台 [9] |
| 可观测 | 平台采集 run/message/tool ID、调用、成本和审计；checkpoint 非产品事件日志 [2，设计推论] | 消息流、usage、OpenTelemetry 可复用，关联、脱敏和产品投影由平台负责 [4][7] | 复用事件思路，统一云端事件/审计仍需平台；未验证完整运行链 [8] |
| 权限 | 平台强制工具授权、工作空间与凭据隔离；提示词/图非安全边界 [1，设计推论] | permission mode/can_use_tool 是钩子；租户、网络和沙箱边界仍由平台 [4] | Worker 独立权限及目录校验已核实，不证明云端多租户隔离 [9] |

## 官方来源

1. [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling)，明确应用执行并回传结果。
2. [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)，checkpointer 线程状态和 store 跨线程数据；原 durable-execution 地址实际重定向到此。
3. [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)，明确恢复重跑节点，interrupt 前副作用应幂等；确认后副作用也可能在成功但 checkpoint 未完成时被重放。
4. [Claude Agent SDK Python API](https://code.claude.com/docs/en/agent-sdk/python)，已读实际 query/interrupt/set_model/stop_task/output_format/can_use_tool API。
5. [SDK sessions](https://code.claude.com/docs/en/agent-sdk/sessions)。
6. [SDK external session storage](https://code.claude.com/docs/en/agent-sdk/session-storage)。
7. [SDK subagents](https://code.claude.com/docs/en/agent-sdk/subagents)、[OpenTelemetry](https://code.claude.com/docs/en/agent-sdk/observability)。
8. [已核实任务调度](../../../docs/research/任务与消息调度.md)；官方固定提交[stop](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/apps/desktop/src/main/maker-ipc/agent-input-coordinator.ts#L3060)、[drain](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/apps/desktop/src/main/maker-ipc/agent-input-coordinator.ts#L4938)。
9. [已核实协作](../../../docs/research/Agent协作对齐.md)；官方固定提交[创建模型与权限](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/apps/desktop/src/main/maker-ipc/orcaWorkerCreationService.ts#L1612)、[成员存储](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/apps/desktop/src/main/localDb/orcaTeamStore.ts#L405)。复用已核实来源，未重新运行上游。

## 权衡与建议（非路线决定）

原生循环/图需要更多模型适配、事件与工具实现，但跨供应商和双模式状态控制最明确。SDK 减少循环/工具/会话初始工作，成本转移到运行依赖、沙箱、会话迁移与供应商范围。外部引擎可借鉴成熟调度和协作，成本集中在桌面宿主拆分、事件语义归一及上游升级维护，不能假定源码多即改造便宜。

建议平台持有任务、阶段、确认、成员身份、权限和事件契约；执行器负责轮次。状态图可只组织业务阶段，无需每 token 成为状态。最终路线由用户选择。

三路线均须稳定 operation ID、执行账本、外部系统去重/核对；工具成功但结果未记账时崩溃，checkpoint 无法单独保证恰好一次。不支持幂等的工具只能标结果不明并核查。区分停止请求、已停止、外部效果待核实；文件回滚不回滚网络效果。以上为 API 边界导出的平台设计建议。[3][4][8]

## 未知与后续运行验证

未取得性能、费用、质量、完整故障恢复实测，不给容量或价格数值。验证：审批挂起重启；工具生效但记账前崩溃；取消后迟到输出；SDK 会话搬迁后的文件与子 Agent；跨供应商工具历史兼容；成员重复指派和写冲突。Claude Managed Agents 是不同托管产品，不将其 server session 能力算入 Agent SDK。供应商网关及模型字符串不是兼容性证明。
