# 云序：目标驱动的个人 Agent

Status: implementing

实施范围扩展见 product-parity-roadmap.md；实际交付与未完成项见 implementation.md。

## 产品定位
云序围绕用户的长期目标持续工作：接收事件，判断目标是否需要推进，在授权和预算内执行，依据结果更新目标；只有需要决策或出现重要变化时打扰用户。

项目保留为资料与执行工作空间，任务保留为一次有边界的执行单元。新增 Goal 作为跨任务的持续承诺：任务交付不等于目标达成。

## 已有基础与缺口
现有 store.py 保存任务、执行、事件、审批和成果；automations.py 已提供日程、渠道去重和发件箱；workflow/orchestrator 继续承担执行。现有 events 依赖 task_id，不能直接当作账户级事件入口。缺少独立目标生命周期、事件到目标的路由、目标评估与持续推进记录。

## 首版闭环
用户创建目标并给出成功标准，例如“本周五之前完成项目演示”。云序记录下一行动与复查时间；到期、用户补充或任务结束产生事件；平台判断是否唤醒目标；执行器推进一次任务；评估证据后选择继续、等待输入、延后复查或完成。服务重启后仍可恢复等待中的目标。

## 目标契约
Goal 包含 owner_id、title、desired_outcome、success_criteria、project_id、status、revision、next_review_at、priority、execution_policy、created_at、updated_at。首版一个目标绑定一个项目，账户可拥有多个目标。
状态为 draft / active / waiting / paused / completed / cancelled。waiting 必须记录等待对象和可恢复条件。completed 必须附成功标准对应的证据；无证据时保持 waiting。取消和暂停禁止后续自动派发，旧版评估不能覆盖新修订。
执行策略包含允许的工具范围、单次时限、每日自动运行次数和失败重试上限；预算不足转为 waiting 并说明原因，不重复唤醒。

## 事件与推进
持久 goal_events 保存 owner_id、source、source_event_id、kind、goal_id、payload、occurred_at、received_at、status、attempts、available_at。以 owner_id + source + source_event_id 唯一约束去重。首版只接收 goal.review_due、user.input、task.finished、task.failed；外部任意 webhook 后续实现。
事件落库后才派发；每个目标同时最多一个推进过程，项目执行沿用现有串行规则。评估保存 event_id、goal_revision、decision、reason、task_id、evidence。相同事件不得重复创建任务；任务完成回流事件，下一步必须带进度依据、等待条件或未来复查时间，避免完成事件无限触发新任务。
遵循 ADR 0003 单进程 SQLite 模型；不引入消息中间件。事务提交派发意图后调用现有执行路径；启动时补派未发送意图，执行状态不明沿用人工检查策略，不盲目重跑外部操作。

## Personal agent 体验
首页展示目标、下一步、等待原因与需要用户处理的事项。用户可追问“为什么现在做这件事”，回答需关联事件、判断和执行结果。保留项目与任务入口作为明细。
个人上下文先复用账户长期指令与现有已授权记忆。跨项目事实自动积累、邮箱和日历接入不属于首版；所有检索按用户和授权范围隔离。个人 Agent 的关键是持续承接目标，而非汇总所有隐私数据。
通知只在目标完成、需用户决定或重要失败时生成；同一原因去重，普通进度留在时间线。既有渠道的发送授权继续适用。

## 验收
1. 一个目标跨两次任务推进，任务一完成后目标仍 active，成功证据满足后才 completed。
2. 重复事件与进程重启不重复创建任务；等待事件可恢复。
3. 暂停、取消、目标修订与并发事件不会产生过期派发或覆盖新状态。
4. 不同账户不能读取、关联或触发对方目标、事件、记忆及成果。
5. 连续失败和无进展循环受预算约束，且界面明确等待原因。
6. 没有模型凭据时报告不可执行，不能生成虚假推进结果。
7. 自动化旧接口及现有任务流程保持兼容。

## 实施顺序
见 issues/01-goal-runtime.md、02-event-loop.md、03-personal-home.md。首版完成后，再用真实个人目标验证一周，决定是否加入邮箱、日历及跨项目长期记忆。
