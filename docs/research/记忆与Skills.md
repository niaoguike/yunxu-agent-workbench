# 记忆与 Skills：固定提交源码核验

本页保留第一轮研究及当时建议。当前产品行为以 PRD v0.2 为准，补充策略见本目录《个性化与记忆对齐》和《工具扩展对齐》，不要把早期候选策略当作最终选择。

基准：`a5bd13c69b9f984ebee5fe32c737e489b6ab47dc`。本轮读取既有源码快照中的实现，未运行上游项目。本代理的固定 raw 下载尝试未取得内容；后续补读已下载的宿主文件，但 Skills 授权/安装链仍保留未知，不能据此承诺产品行为。

## 1. 记忆存储必须隔离账户与项目

**事实**：`MakerMemoryManager.getStore` 先检查 owner，再按工作目录 scope 取 store；owner 变化清池并换根，owner 不可用时拒绝临时目录回退；SSH 使用包含 host ID 的复合键。`assertScopeUnchanged` 在异步路径阻止跨账户结果提交。

证据：[manager.ts / ensureOwnerScope、assertScopeUnchanged、getStore，L239–286、390–400、518–570](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/packages/maker-core/src/memory/manager.ts#L518)。

**产品建议**：记忆归属账户，项目记忆以平台项目 ID 隔离，不能直接以容器路径当产品项目标识。

**验收**：同项目两个任务读到共同项目记忆；不同项目与账户不能互读；写入过程中切换账户，写入失败且两侧数据都不串号。

## 2. 开关、清理与删除是不同动作

**事实**：`enable` 标记开启并调用各 agent 的 `setMemory(false)`，返回 next-session；`disable` 仅关闭统一记忆，不恢复引擎原生记忆。`resetWorkdir` 明确允许关闭后清理；`resetDigests` 与 `resetAll` 分开实现，全局清理排除独立 Bot scope。

证据：[manager.ts / enable、syncNativeAgentsOff、disable，L443–498](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/packages/maker-core/src/memory/manager.ts#L443)、[resetWorkdir、resetDigests、resetAll，L734–889](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/packages/maker-core/src/memory/manager.ts#L734)。

**产品建议**：首版明确提供关闭记忆、清空项目记忆两个入口；摘要重建属于维护能力，不与用户删除混用。

**验收**：关闭后历史记录仍可清空；清空项目 A 不影响 B；新任务使用新开关值；运行中任务显示生效边界。

## 3. 模型调用链影响记忆审查，不能等同于存储能力

**事实**：`runReview` 读取 store 后构造审查提示，优先宿主注入的 reviewOneShot；显式路由禁用时拒绝执行。宿主注入链没有产出时不随意回退默认 agent。`write` 自身通过 getStore 后写入，并不是审查调用。

证据：[manager.ts / runReview、write，L900–968](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/packages/maker-core/src/memory/manager.ts#L900)。

**产品建议**：项目记忆作为平台能力保持稳定；审查/整理失败独立提示，不把模型供应商错误解释为记忆丢失。

**验收**：禁用审查模型后已有记忆仍保留；审查失败不覆盖内容；切换模型不改变项目记忆归属。

## 4. Skills 有会话加载快照，更新不保证运行中生效

**事实**：`mergePiRuntimeSkillStatuses` 比较 manifest 中已加载 Skills 的内容摘要与当前来源指纹；变化后生成“restart the session”错误。`mergePiRuntimeSkillStatuses` 使用运行会话快照映射 managed skills，避免新安装或重命名技能在会话中被误报为已可执行。宿主 `Maker.listAgentSkills`（L1697–1727）调用该合并函数，使用 session.getRuntimeCapabilities() 取得会话能力快照。

证据：[maker.ts / mergePiRuntimeSkillStatuses，L300–410](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/packages/maker-core/src/maker.ts#L300)。

**产品建议**：区分已安装、已启用、本任务已加载；更新后提示新任务生效或显式重启。

**验收**：任务启动后修改技能，当前任务不宣称加载新版本；新任务加载更新版；重命名不产生错误的可执行状态。

## 5. 能力接入取决于执行引擎和宿主装配

**事实**：`Maker` 保存注入的 makerMemory；BaseAgent 启动选项同时有 makerMemoryEnabled、makerMemoryScopeKey、makerMemoryIndexSnapshot，说明开关、作用域、索引快照是不同输入。Pi Skills 投影另有引擎专用处理。

证据：[maker.ts / constructor，L546–567](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/packages/maker-core/src/maker.ts#L546)、[base-agent.ts / StartSessionOptions，L2010–2030](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/packages/maker-core/src/agents/base-agent.ts#L2010)。

**产品建议**：先定义平台统一能力契约，再为引擎做适配与能力检测；不因用户选择模型就自动承诺 Skills 与记忆完全一致。

**验收**：每个上线引擎执行同一项目记忆隔离、技能版本和禁用测试；不支持时提供明确状态。

## 尚未核实的参考实现

- 记忆工具实际 read/search/write/delete 到 store 的完整调用者，以及自动记忆何时触发：仅确认 manager 的写入入口，尚未取得 MCP 工具实现。
- Skills 的发现目录优先级、首次授权、扫描许可、安装更新原子性和禁用后运行中撤销：尚未取得各实现与宿主调用者。
- 切换引擎后的记忆索引格式兼容与运行中模型切换行为。

这些空白不能从文件名、README 或功能名称推导为参考项目已实现；本产品可以独立定义需求，但必须自行实现并验收。


## 宿主补证据

已读取固定提交的 `apps/desktop/src/main/maker-host/maker-memory-host.ts`。`createDesktopMakerMemoryManager`（L68–126）实际构造 manager：`resolveBasePath`（L73–80）在账户边界期间或没有 owner 时返回 null；正常时使用 `userData/owners/<owner hash>`；`ownerScopeKey`（L88–92）包括账户、generation 和 boundary；L103–104 按当前 owner 根重读开关，L108–113 为 Bot 独立目录注入 resolver。以上使第 1、2 项从接口约定补到了宿主装配实现。

固定证据：[maker-memory-host.ts / createDesktopMakerMemoryManager](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/apps/desktop/src/main/maker-host/maker-memory-host.ts#L68)。验收：账户切换边界中拒绝新记忆读写，切换完成按新账户开关和目录访问。

Skills 投影的实际函数为 `mergePiRuntimeSkillStatuses`（L305）；宿主调用点为 `Maker.listAgentSkills` L1727。

## 6. 项目插件开关有宿主写入边界，但不能推广为 Skills 授权

**事实**：`registerProjectPluginPolicyHandlers`（L26–61）注册 `PLUGINS_SET_PROJECT_ENABLED` 与 `PLUGINS_CLEAR_PROJECT_ENABLED`，校验参数后实际调用 `getPluginRegistry().setProjectEnabled(id, workingDir, enabled)`（L43）或 `clearProjectEnabled(id, workingDir)`（L54）。registry 返回 false 时转为 PERMISSION_DENIED，错误说明涉及 essential plugin。handler 内没有活动任务重启或热卸载调用；文件注释明确其用于未来会话创建。此证据确认的是项目插件策略写入消费链，不是 Skills 首次执行授权或完整发现加载链；尚未追到 registry 内部和启动时读取端。

固定证据：[projectPluginPolicyHandlers.ts / registerProjectPluginPolicyHandlers，L26–61](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/apps/desktop/src/main/maker-ipc/projectPluginPolicyHandlers.ts#L26)。路径须与固定提交树对应，不能将插件与技能视为同义词。

**产品建议**：插件启停明确标注项目范围和下一任务生效；Skills 启用与敏感操作授权分别定义。

**验收**：项目 A 关闭普通插件，新任务按策略加载；项目 B 不受影响；必要插件拒绝修改；已有任务的状态不被界面误报为已热卸载。实际启动加载端仍需后续核验。
