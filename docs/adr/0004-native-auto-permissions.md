# ADR 0004: 复用原生 Auto 权限审核

状态：已实现。2026-11-03。

用户反馈逐次工具授权打断任务，并确认对齐参考实现。工具授权与需求、方案、验收等业务确认分开：任务持久化 `permission_mode=auto|ask`，默认 Auto；配置写入运行快照。修改策略要求当前执行先停止，新会话显式使用新策略，不把授权热更新伪装成已经生效。只修改工具授权不改变需求版本、阶段或待验收确认；改变执行器、模式等仍使用原有的重新评审规则。

## Codex

采用 `approvalPolicy=on-request`、`sandbox=workspace-write`，Auto 设置 `approvalsReviewer=auto_review`，Ask 显式设置 `user`。这是所读参考代码的实际映射，并非 `never + danger-full-access`。每次 thread start/resume 与 turn start 均显式覆盖 reviewer，避免从旧会话继承宽松配置。单轮明确限定工作区写入根，默认关闭沙箱网络。

协议不支持 reviewer 时，回退受限沙箱内人工审核，记录诊断。原生自动审核拒绝、超时、取消均记录，不转换成批准；用户可在停止后切换 Ask 并明确新的授权。原生风险分类不是形式化安全证明，trusted-local 也不因此成为多租户安全环境。

常规工作区工具活动与原生 `item/autoApprovalReview/*` 事件进入平台持久事件。界面显示审核状态、风险与理由，而不是伪造一张已经获用户同意的卡片。

## Claude

Auto 使用 SDK 原生 `permission_mode=auto`。已知原生工具交给原生分类器；PreToolUse 不再无条件弹窗。项目外/受保护文件路径、未知或自定义 MCP 工具仍须人工授权，原生回调提出的剩余授权同样转为人工。Ask 保持逐工具确认。禁用用户/项目原生配置源，避免继承 bypass。Claude 原生 Auto 的带凭据链尚未实测，不据此声称双引擎已验证。

## 证据

- [参考项目固定版本 codex/index.ts L604–645](https://github.com/makecindy/cindy/blob/a5bd13c69b9f984ebee5fe32c737e489b6ab47dc/packages/maker-core/src/agents/codex/index.ts#L604)：本地源码快照 `.references/harness-alignment/codex-agent.ts` 的 `mapPermissionToCodex`。
- 已安装 Codex `0.166.0-alpha.5.1` 导出的 `ThreadStartParams`、`TurnStartParams` 和 `ApprovalsReviewer` schema（临时文件 `.data/codex-schema`）。协议说明 `auto_review` 使用上下文与风险框架进行自动批准/拒绝。
- 真实 Codex + trusted-local 在独立工作区创建并读取 `auto-review-check.txt`，得到 completed；人工授权请求数为 0。该测试只证明常规工作区路径，不代表危险操作拒绝链已全量实测。
