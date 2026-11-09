# 本地自动化与聊天渠道

当前实现运行在本地单进程后端，使用同一个数据目录保存计划、执行记录、渠道消息、任务关联和发送结果。页面右侧有“自动化”“渠道”；插件包位于“扩展”。无需部署公网服务。

## 自动化

1. 打开项目 → 自动化，填写名称、执行内容、每日/每周、时间和时区（北京时间或 UTC）。
2. 选择 Codex/Claude 与 Ask/Auto。默认停用、Ask；勾选启用后按计划运行。
3. 可选择结果回传渠道；该连接需要另外配置密钥并启用发送。留空时只在工作台查看。
4. “立即执行一次”不改变下次计划时间。每次运行生成新主任务，点击执行记录中的“打开任务”查看对话、审批、检查与成果。
5. 可编辑计划、暂停、恢复。暂停保留所有记录；恢复从下一个计划时间开始。

同一时间槽只派发一次。上次自动化任务仍在执行、暂停或等待审批/确认时，本次跳过并留记录。超过一分钟错过的计划不会补跑；重启时记录最近错过的时间槽并移到下次，不重放全部历史时间槽。后台关闭或电脑休眠时不能执行。执行器不可用时保留失败记录、任务和输入，不生成假回复；配置执行器后可打开任务手动继续。

## Telegram

1. 通过 BotFather 创建机器人，先向机器人发送消息。准备 Bot Token、目标聊天的数字 ID、允许触发任务的用户数字 ID。
2. 在渠道中新建 Telegram 连接。默认不接收、不发送、不启用，分别勾选所需权限。
3. 保存后展开“连接配置说明”，复制此连接专属的 `WORKBENCH_CHANNEL_<连接ID>_TOKEN` 变量名。在本机 `.env` 添加该变量，值为 Bot Token。**不要把密钥粘贴到任务、插件 JSON 或聊天里。**
4. 重新启动后端，点击“验证机器人身份”。此按钮只调用 getMe，不会发送消息；验证成功不代表目标聊天的全部权限已经验证。
5. 编辑连接，勾选接收/发送和启用。只有匹配指定聊天与用户的文本会创建任务，机器人、附件和其他用户不触发任务。

接收使用 getUpdates 长轮询，游标持久化。启用后会处理机器人仍保留的未确认消息；只应使用专门用于此工作台的机器人。群聊中还需确保机器人的隐私设置允许接收目标消息，或回复机器人的消息进行续聊。不能与同一机器人的 webhook 或其他轮询程序并用，应用不会自动删除既有 webhook。详情见 [Telegram Bot API](https://core.telegram.org/bots/api#getupdates) 和 [官方 FAQ](https://core.telegram.org/bots/faq)。

在渠道中发新消息创建新任务；回复之前的用户消息或机器人结果，可继续原任务。工作台“交接当前任务到此渠道”发送当前任务摘要，回复它继续原任务。已有运行时补充排队，沿用工作台工具审批规则。

## Slack

1. 创建 Slack App 并开启 Socket Mode；生成带 `connections:write` 的 App-Level Token。
2. 配置 Bot Token：发送需要 `chat:write`；公共频道接收订阅 `message.channels` 并授予 `channels:history`；私聊接收订阅 `message.im` 并授予 `im:history`。需要私有频道时使用相应事件及权限。将 App 安装到工作区并加入目标聊天。
3. 在渠道中新建 Slack 连接，填写频道/聊天 ID 和允许的 Slack 用户 ID。一个连接只接受指定用户在指定聊天中的消息。
4. 在“连接配置说明”中复制两个环境变量名：`..._TOKEN` 填 Bot Token，`..._APP_TOKEN` 填 App-Level Token；写入本机 `.env` 并重启后端。
5. 验证机器人身份（auth.test），再按需要启用接收/发送。身份检查不替代 Socket Mode 和发送权限的实际验收。

Socket Mode 使用后端主动建立的 WebSocket，无需公开回调地址；断线会重连。消息按频道和消息时间戳去重，线程回复继续原任务。仅处理普通文本/提及事件，不处理机器人消息、编辑/删除等子类型。

官方依据：[Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode/)、[connections:write](https://docs.slack.dev/reference/scopes/connections.write/)、[chat.postMessage](https://docs.slack.dev/reference/methods/chat.postMessage/)、[message.channels](https://docs.slack.dev/reference/events/message.channels)、[message.im](https://docs.slack.dev/reference/events/message.im)。

## 收发与恢复边界

- 同一机器人只启用一个接收连接，避免多个轮询器/订阅重复消费。连接的聊天、用户和平台保存后不可改；换目标请新建连接。
- 允许发送后，任务结束回传状态、文本摘要及成果名称；不公开项目文件或建立公开下载链接。人工审批仍在工作台处理。
- 待发送先落库，发送中断或超时/服务端错误标记“结果不明”，不自动重发。先检查外部聊天；只有明确失败记录提供手动重试，防止重复消息。
- 停用连接会停止后续接收和发送，不撤回已发送内容、不停止已接受的任务；重新启用发送时仍待发送的记录可能继续处理。
- 项目回收后不再执行计划或收发消息。任务、计划和记录仍随数据目录保留。
- 密钥不写入数据库、不在 API 回显、不进入错误记录；原生执行器不继承 `WORKBENCH_CHANNEL_` 环境变量。trusted-local 本身仍是可信个人执行模式，并非恶意代码隔离边界。
- 接收先持久化再确认，随后派发；服务在中间中断时保留不明记录供检查，不盲目重跑。

## Skills、MCP 与插件包

单个 Skill / MCP 沿用“扩展”中的配置。插件包是包含多个扩展的 JSON，示例：

```json
{
  "name": "csv-helper",
  "version": "1.0",
  "source": "本地方法包",
  "extensions": [
    {"name": "analysis", "kind": "skill", "content": "分析 CSV 前检查字段、缺失值和筛选条件。"}
  ]
}
```

导入时完整校验、原子写入且强制停用。名称加插件前缀，冲突则拒绝；同版本不能重复导入。界面展示来源声明、版本、原内容和 SHA-256 完整性摘要，**不把哈希标为安全审核**。查看后可整体启停；停用沿用现有的停止执行及会话失效规则。

MCP 仍仅支持执行环境内的 stdio command/args，不在插件中接收明文密钥。导入配置不安装任意系统软件，也不代表对应服务已可用。本次不包含远程市场、OAuth 授权向导或插件升级/回退。

## 验证

```powershell
.venv/Scripts/python.exe -m pytest -q
npm.cmd test --prefix frontend
npm.cmd run build --prefix frontend
# 独立测试服务使用禁用执行器和独立 WORKBENCH_DATA，地址默认 127.0.0.1:8768
.venv/Scripts/python.exe tests/browser_automations.py
```

渠道自动测试使用离线运输夹具，验证接口形状、用户限制、消息去重、线程关联、重启状态与发送错误处理。浏览器验证配置、计划、执行记录、重开和插件启停。未使用真实渠道密钥，真实 Slack/Telegram 收发及定时真实模型交付仍需配置后验证。
