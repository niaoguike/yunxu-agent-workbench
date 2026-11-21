# 云序 Personal Agent 开发版本

本版本在现有工作台上增加账户主助手、目标与持续责任、事件复查、邮件/日历连接器及持久浏览器适配。现有项目、原生执行器、权限和持久协作者继续使用。

## 使用

登录后默认进入“我的个人助手”，无需先创建项目。主助手使用独立的个人项目和任务，默认 Codex、Ask 工具授权、开启既有持久协作。账户长期指令继续从左侧维护；记忆、渠道、模型和协作者在“查看执行、授权与协作者”进入原工作台配置。

交办普通工作使用主对话。明确的长期承诺可从目标表单创建，或由助手使用 goal_create 保存草稿，用户启动。选择“持续责任”或“条件关注”后，成功执行会安排下一次复查；失败和预算耗尽进入等待。条件是否成立由执行器依据资料判断，平台不伪造判断结果。

目标完成由用户逐条提交并确认成功标准的证据说明；这不是独立机器核验。暂停/取消停止后续目标派发，已开始的执行需要在任务页停止。任务返回结果后，普通目标默认等待检查；执行器可使用 goal_schedule 安排下一行动和复查。没有新行动不自动创建无限任务。

## Google 邮件与日历

安装可选运行依赖：

```powershell
.venv/Scripts/python.exe -m pip install -e '.[personal,test]'
```

管理员配置以下环境变量后重启 API，值应通过部署密钥管理器注入，不提交仓库：

- WORKBENCH_VAULT_KEY：Fernet 密钥；使用 cryptography.fernet.Fernet.generate_key() 生成。长期保存，轮换前应迁移已存凭据。
- WORKBENCH_GOOGLE_CLIENT_ID / WORKBENCH_GOOGLE_CLIENT_SECRET：Google OAuth Web 应用身份。
- WORKBENCH_GOOGLE_REDIRECT_URI：精确注册为 API 的 `/api/personal/oauth/callback` 完整地址，浏览器需要携带同站登录 cookie。

Google 应用须启用 Gmail 与 Calendar API，并按部署范围配置 consent screen 和测试用户。主页点连接，完成 Google 授权后返回主页。账户授权使用 state、PKCE，凭据加密存于工作台数据库，不注入 Agent 提示词、项目文件或协作者。

首轮 Gmail 拉取最近 50 封邮件的元信息；后续使用 historyId。日历使用 syncToken 和分页。每 60 秒尝试同步；错误停止自动同步，用户手动重试。过期游标清除后需手动重试全量初始化。主页选择目标订阅连接变化，未订阅目标不会被应用数据唤醒。

主助手可读取已同步资料，提出邮件或新建日程请求。主页完整展示待发内容，用户批准后执行；拒绝、撤销连接后不可执行。结果不明不自动重发。当前不提供编辑/删除已有日程、附件全文检索或通用连接器市场。

官方协议参考：
- https://developers.google.com/identity/protocols/oauth2/web-server
- https://developers.google.com/workspace/gmail/api/guides/sync
- https://developers.google.com/workspace/calendar/api/guides/sync

## 持久浏览器

需要运行中的 Docker，构建镜像并设置：

```powershell
docker build -f Dockerfile.browser -t yunxu-browser:local .
$env:WORKBENCH_PERSONAL_COMPUTER='docker'
```

主页启动电脑，为账户创建浏览器容器和持久卷。容器不挂宿主目录、Docker socket、个人认证或控制面凭据。仅控制面调用 Docker；CDP 不发布端口。用户可刷新截图、点击、输入和按键接管登录，暂停后保留浏览器资料。主助手可观察截图及提出电脑操作，用户逐次批准后执行；协作者没有电脑访问权。

这是开发用的浏览器环境，不是完整桌面或已验证的多租户云电脑。Chromium 依靠容器边界运行，尚无独立网络出口审查、私网访问隔离证明或生产级容器生命周期/备份。不得据此宣称拥有 Muse Sentinel 等级的边界。当前直接导航只允许 HTTPS 域名，网站内部跳转与 DNS 仍需要部署级出口限制。

## 渠道、语音与协作

在个人项目配置现有 Slack/Telegram 连接，渠道消息进入同一个主助手任务；既有接收身份、发送开关和去重规则仍有效。真实渠道发送依赖服务端密钥与账户授权。

语音入口使用浏览器语音识别，识别后由用户检查并发送文字；支持情况取决于浏览器。它不是全双工实时语音通话，页面已明确提示使用浏览器语音服务。

主助手沿用持久协作者机制；主页汇总个人项目中的协作者报告，可打开任务检查成果。个人连接器与浏览器工具只向主助手开放，工作成员不继承账户访问权。

## 验证边界

协议夹具与测试验证平台状态、接口和权限，不能替代真实模型、Google OAuth、Docker 浏览器和渠道联调。当前环境 Docker daemon 未运行，未提供 Google OAuth 应用，故这些真实路径未验收；完整实时语音、通知安静时段和跨项目自动个人记忆仍未实现。

变更与实际检查结果见 `.scratch/personal-agent/implementation.md`。

## 原生执行器补充

原生 Codex MCP 工具审批通过 `mcpServer/elicitation/request` 接入云序授权列表，仅识别 MCP 工具审批；普通结构化表单保持拒绝。实现依据本机生成的 app-server JSON Schema 及 https://github.com/openai/codex/blob/main/codex-rs/core/src/mcp_tool_call.rs 。

Windows 的 worker 与 MCP stdio 使用显式 UTF-8，Google 应用密钥和 Vault 密钥不继承到 worker。可信本机执行仍共享宿主身份，环境变量过滤不等价于宿主文件访问隔离。

真实模型检查在隔离测试账户下，经 Ask 审批创建中文目标草稿成功；没有激活目标或操作 Google/渠道账户，也没有因此证明完整目标执行与交付通过。
