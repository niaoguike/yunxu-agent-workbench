# Personal Agent 官方能力对标

核验日期：2026-11-21。范围仅为指定官方公开页面；产品描述代表公开承诺，不等于已独立验证服务效果。

## 共同方向

三者均把 Agent 定位为持续协作的个人代理：通过消息交办工作，保留上下文，在云电脑上跨工具执行并在后台继续推进。Muse 强调认识用户与生活事务，dots 强调持续责任与跟进，Grok Bot 强调多个同事式 Bot 协作。[Muse](https://ai.meta.com/muse/) [dots 发布](https://openai.com/index/introducing-dots/) [Grok Bot 发布](https://x.ai/news/introducing-grok-bot)

## 关键差异

| 产品 | 公开可确认的重点 | 对云序的启发 |
| --- | --- | --- |
| Muse | 用户与代理共享持久隔离 Linux VM；安全博客明确 runtime 隔离、凭据代理与独立 Sentinel，后者控制连接器动作和网络出口。 | 执行能力必须配套独立授权边界。 |
| dots | 账户主 dot 跨对话持续承担责任；自行暂停和唤醒；后台代理并行；使用记忆、笔记，委派 Work/Codex；云电脑可接管，本地电脑连接有独立权限。 | 主 Agent 应管理责任、事件、记忆和委派，而不仅是一次任务。 |
| Grok Bot | 多 Bot 共用持久云电脑、浏览器、文件与终端；Bot 相互交接，汇总交给用户；支持语音、连接器、自定义 MCP/API、技能与日程。 | 多代理的价值在实际协作与交付，不只展示多个角色。 |

表格来源：[Muse 产品页](https://ai.meta.com/muse/)、[Muse 安全设计](https://muse.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse)、[dots 官方文档](https://learn.chatgpt.com/docs/dots)、[Grok Bot 官方文档](https://docs.x.ai/grok-bot/overview)。

## 权限不能只靠提示词

Muse 明确由独立 Sentinel 允许、拒绝或请求批准；代理只见替代凭据，真实凭据在授权后的网络边界注入。[安全设计](https://muse.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse)

dots 区分聊天渠道、应用连接与本地电脑权限，并对影响账户或共享信息的动作进行审查；用户自定义指令不能覆盖内置安全要求。[官方文档](https://learn.chatgpt.com/docs/dots)

Grok 的公开发布描述需批准时返回用户；这并不能证明其内部采用与 Muse 相同的 Sentinel 架构。[发布说明](https://x.ai/news/introducing-grok-bot)

## 公开资料无法确认的内容

不能由这些页面推出完整系统提示词、模型训练配方、记忆检索算法、事件队列与调度策略、数据库完整 schema、故障恢复机制、生产容量、任务成功率，或三者使用相同内部架构。Muse 披露了部分安全组件，并不代表整个实现已公开。只能对齐可观察能力与已披露约束，不宣称一比一内部复刻。

## 对云序的建议（设计判断）

以“账户主 Agent + 持续责任与事件 + 个人记忆 + 工具/电脑执行 + 独立权限”作为主线。Goal 面板可以承载目标与进度，但仅有该面板仍是骨架；应补齐事件唤醒、跨轮跟进、可追溯委派和结果验收，再扩充连接器与电脑能力。此段是根据产品资料提出的设计建议，不是对云序现有代码完成度的独立审计。
