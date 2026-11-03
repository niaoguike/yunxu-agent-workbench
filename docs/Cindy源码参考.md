# Cindy 源码参考：产品策略

核查日期：2026-11-03。基准提交：`a5bd13c69b9f984ebee5fe32c737e489b6ab47dc`。

## 获取方式与证据范围

普通浅克隆无数据后停止，官方完整 ZIP 下载超时，随后过滤克隆成功取得提交和目录树，位置 `.references/cindy-sparse/`，未完整检出。八份关键源码通过官方 raw 地址下载到 `.references/source-snapshots/`，逐一用 `git hash-object` 对比基准提交的 `git ls-tree` blob ID，全部一致。包括 maker.ts、session-storage.ts、memory/manager.ts、orca-bridge-mcp.ts、orca-bridge-prompt.ts，以及后续补取的 session.ts、agents/base-agent.ts、agents/shared/tool-loop-review.ts。

参考目录通过根目录 `.gitignore` 排除，不作为本项目源码提交。未安装 Cindy 依赖或运行其程序；未验证真实设备互联、底层工具数据流及云端后端行为。

## 1. 项目、工作目录、执行引擎与运行设备分开建模

证据：[session-storage.ts](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/packages/maker-core/src/interfaces/session-storage.ts)，`SessionMeta`；[maker.ts](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/packages/maker-core/src/maker.ts)，`CreateSessionOptions`、`createSession`。

`SessionMeta` 分别记录 projectDir、workDir、agentKind、remoteHostId、agentDeviceId。`createSession` 在 agentDeviceId 存在时拒绝同时指定 remoteHostId，并调用宿主注入的 startDeviceAgentSession；否则调用相应引擎 startSession。

源码描述另一设备运行 Agent 时，项目文件与命令仍可留在本机，执行引擎使用另一设备的程序、登录与供应商配置。已验证路由分支，尚未追完宿主实现，不能宣称完整跨机工具桥已核实。

产品启发：分别定义项目、工作空间、运行环境和执行引擎；连接另一设备和把整个项目移到另一设备不是同一操作。默认云端或默认用户电脑，仍是本产品要确认的选择，不能由参考源码自动决定。

## 2. 操作按当前会话的实际能力呈现

证据：maker.ts 的 `capabilitiesForSession` 与创建 Session 的调用。

代码根据引擎、运行路由与 handle 的 commitRewindFiles 等实际能力决定是否支持回退，并提供不可用原因。由另一设备运行 Agent 的会话，与 SSH 远端会话也有不同处理。

产品启发：能力不足时明确解释，不用统一按钮暗示所有环境都能回退或恢复；切换模型、切换引擎、切换运行环境应分别处理。具体首版提供哪些入口仍待确认。

## 3. 运行结束与产品归档分开

证据：session-storage.ts 关于移除历史 status 的说明。

源码记录了进程瞬态状态曾与产品 active/archived/deleted 混淆的问题，并明确将运行态与产品持久状态分离。

产品启发：停止任务、关闭页面、归档、删除分别定义；停止执行不应自动归档或丢弃成果。这里借鉴状态语义，不照搬其内存状态存储方式。

## 4. Skill 已安装与本次已加载分开

证据：maker.ts 的 `mergePiRuntimeSkillStatuses`、`listAgentRuntimeSkills`。

Pi 路径区分 approved 与 loaded；会话启动后通过 snapshotDigest/sourceFingerprint 判断 Skill 是否变化，变化时提示重启会话加载当前版本。

产品启发：任务显示实际使用的 Skill 版本，更新方法包时说明何时生效，运行中的任务不静默替换方法。该结论针对已读 Pi 路径，不泛化成所有执行引擎的共同实现。

## 5. 记忆有作用域、开关与生效时间

证据：[memory/manager.ts](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/packages/maker-core/src/memory/manager.ts)，`MakerMemoryManager`。

构造器 initialEnabled 缺省为 false；管理器维护按工作目录的 Store，统一记忆与引擎原生 auto-memory 互斥。owner 作用域不可用时拒绝访问，作用域变化会关闭旧 Store；开关结果包含 next-session 的生效说明。

产品启发：明确记忆归属、范围、清理方式及生效时机。不能从这些代码推断“每条记忆写入都必须用户确认”，这一规则仍需本产品决定。

## 6. 持久协作者与临时子 Agent 分开

证据：[orca-bridge-prompt.ts](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/packages/orca-workflow/src/orca-bridge-prompt.ts)、[orca-bridge-mcp.ts](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/packages/orca-workflow/src/orca-bridge-mcp.ts)。

Lead/Worker 提示明确区分具有独立持久会话、用户可见的 Orca Worker 与执行引擎内部临时 subagent。桥接实现区分 dispatched、queued、steered 结果；queue 与 steer 的投递意图不同，不能将“已排队”显示成“已开始执行”。

产品启发：自主拆分子任务不必自动等同于创建一组长期角色。若提供协作视图，分别显示等待、排队、执行与结果回报。提示词表达的是预期约束，不能据此声称模型必定遵守；完整 UI 和调度链尚未运行验证。

## 7. 审批策略：已核实接口，尚未核实完整消费链

已补取并核对到基准提交的 [base-agent.ts](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/packages/maker-core/src/agents/base-agent.ts) 定义了 `McpToolApprovalPolicy`：auto-approve、prompt、prompt-each-time。审批上下文携带服务、工具与参数；远端 header 配置使用环境变量引用，并约定向普通 shell 派生进程剥离相应凭据。

产品启发：授权要有范围、有效期和具体对象，不能只设计一个“全自动”总开关。这段证明接口与约定存在，尚不证明各执行引擎都以相同方式正确实施。

## 8. 追加消息、立即纠正与停止是不同动作

证据：[session.ts](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/packages/maker-core/src/session.ts) 的 `send`、`dispatchSend`、`steer`、`abort`。

普通 send 在已有运行回合或发送预留时拒绝新的发送，不负责自动排队。steer 显式检查 sameTurnSteer 能力、当前运行状态，并在异步处理后重新检查 turn generation，避免用户补充被投递到另一个回合；不支持会抛出错误，不在 core 自动改成排队。abort 处理未派发请求及在途执行，经过 aborting 后可回到 active，不等于删除任务或归档。

产品启发：我们的消息队列应由产品层明确管理，展示待执行、已投递和已生效状态。立即纠正不可用时解释原因，让用户选择等待或停止后执行。单个会话的执行保护不能据此推出整个项目必须串行，后者属于本项目的独立取舍。

## 9. 重复工具调用不立即等于死循环

证据：[tool-loop-review.ts](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/packages/maker-core/src/agents/shared/tool-loop-review.ts)，`ToolLoopMonitor.onToolResult`、`startReview`。

达到最终上限时直接返回中断判定；其他疑似循环进入有次数限制的辅助复核。复核超时或失败按 continue 处理，同时保留最终上限；目标或调用模式改变时丢弃过时结论。证据使用公开行动说明和工具记录，不依赖模型私有思维链。

产品启发：向用户区分“疑似无进展”和“达到限制已停止”，保留部分结果及继续入口；不要把一次工具重试或复核模型不可用显示成业务任务已经失败。

## 对本项目的使用方式

以上是源码事实及候选产品策略，不构成已获用户确认的 PRD。优先确认默认运行环境，随后确定工作空间生命周期、任务干预与交付、授权、记忆和 Skills 等规则。通用云端工作台的定位及“任务交付为核心、协作为辅助”的已确认决定保持有效。
