# Agent 协作对齐 Cindy

核对基线：`makecindy/cindy@a5bd13c69b9f984ebee5fe32c737e489b6ab47dc`。以下区分源码事实、云端产品建议和仍需验证的部分；不把桌面多设备能力作为云端需求。

1. **持久 Worker 是独立会话。** `OrcaRole` 为 lead/worker；`addWorker` 把 Worker 写入数据库并设置 session role；`getWorkerContext` 可查询 Lead 的模型、目录、SDK session 等上下文。这是可再次联系的会话关系，不能仅用一次工具调用的临时子 Agent 表达。[orcaTeamStore.ts L21、L405–447、L540–575](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/apps/desktop/src/main/localDb/orcaTeamStore.ts#L405)。建议在既有“项目—任务”下增加任务内协作成员及各自会话，保留临时子 Agent 作为另一路径。验收：关闭页面重开后成员、历史和指派关系仍可查看。

2. **团队生命周期与运行状态分开。** `getActiveTeamByLeadSession` 保证读取当前 active team，发现多个时收敛；归档 Worker 保留 role 供历史识别；已无 active team 时有 orphan session 归档兜底。[orcaTeamStore.ts L194–225、L303–380](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/apps/desktop/src/main/localDb/orcaTeamStore.ts#L194)。建议界面区分“空闲/运行中/已完成”和“归档/解散”，完成不应抹掉会话。验收：完成后可追问；归档后历史可查；解散不留下不可见但运行中的成员。

3. **创建并非只拼提示词。** `createOrcaWorkerCreationService` 建立 worker/session 标识，`bootstrapSession(workerOpts)` 启动会话，随后保存记录；创建采用 reservation 租期防并发超限。[orcaWorkerCreationService.ts L1561–1635](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/apps/desktop/src/main/maker-ipc/orcaWorkerCreationService.ts#L1561)。建议用户在任务协作区创建成员、命名职责、查看执行状态并直接指派；Lead 也可通过工具创建。具体 UI 入口与按钮尚未从组件验证，不宣称复刻。验收：重复点击不会留下重复成员或空壳会话。

4. **排队和插话有明确差异。** `OrcaInterAgentDispatchMode` 返回 dispatched/queued/steered；`delivery` 缺省 queue。`dispatchOrEnqueueOrcaInterAgentMessage` 只在显式 steer 且目标正在运行时尝试同 turn 插话；能力不支持或输入边界忙时返回队列及原因。[orcaInterAgentDispatcher.ts L33–67、L504–619](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/apps/desktop/src/main/maker-ipc/orcaInterAgentDispatcher.ts#L504)。建议干预入口提供“下一轮处理”和“立即调整”，展示实际投递结果。验收：不支持 steer 时用户看到已排队；同一 clientId 不重复投递。

5. **恢复必须依赖持久身份。** dispatcher 的唤醒结果区分 resumed/already-active/created/queued，并处理 SESSION_RUNNING 竞争回退。[orcaInterAgentDispatcher.ts L111、L630–710](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/apps/desktop/src/main/maker-ipc/orcaInterAgentDispatcher.ts#L630)。建议云端由会话 ID、运行 ID 和消息 ID 恢复，浏览器连接不拥有执行生命周期。未知：本轮未完整追 stop 的宿主终止链及重启后队列存储；不能由 send/queue 实现推断可靠停止。验收须包含服务重启恢复、停止确认、停止后不自动消费旧队列及用户显式继续。

6. **模型与权限分别解析。** Worker 使用 resolved.model/providerId/effort/fastMode；权限显式调用 `resolveOrcaWorkerPermissionMode(params.workerPermissionMode)`，源码注明不继承 Lead 权限。[orcaWorkerCreationService.ts L1612–1617](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/apps/desktop/src/main/maker-ipc/orcaWorkerCreationService.ts#L1612)。建议创建时展示最终模型和权限策略，云端权限取项目授权与成员授权交集；不可把“继承 Lead 一切”写成事实。验收：高权限 Lead 不会隐式给 Worker 同等权限，模型不可用时明确报错或展示回退。

7. **共享目录不会自动解决并行写冲突。** 默认 workingDir 来自 Lead；显式目录经过校验，可选 worktree 会创建独立目录并再次授权。[orcaWorkerCreationService.ts L1201–1223、L1578–1606](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/apps/desktop/src/main/maker-ipc/orcaWorkerCreationService.ts#L1578)。建议长期项目共享资料，任务与协作成员的可写产物分区；代码任务允许隔离分支/工作树，合并由明确步骤完成。源码不能证明共享目录存在自动文件锁或自动合并。验收：两个成员修改同一产物时产生可见冲突与版本记录，不静默覆盖。
