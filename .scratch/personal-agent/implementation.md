# Personal Agent 实施与验证记录

日期：2026-11-21。当前交付为可运行的开发版本，未完成全部产品对齐及生产验收。

## 实际交付

| 路线 | 已实现 | 边界 |
| --- | --- | --- |
| 1 Goal | 持久化、配置修改、同项目任务关联、版本冲突、用户逐项证据完成、事件去重、暂停竞态与中断核查恢复 | 完成依据用户确认说明，非独立自动评估 |
| 2 主助手 | 每账户持久身份、个人项目和主任务、默认 Ask、长期指令与现有记忆复用 | 跨项目自动个人记忆未实现 |
| 3 事件推进 | 持续责任/关注项、结果回流、复查时间、goal_schedule、次数预算、失败进入等待 | 开放目标语义与无进展判断仍需更多真实场景评测 |
| 4 邮件/日历 | OAuth state/PKCE、加密存储、增量游标、目标显式订阅、主助手读取与提案、用户审批执行、撤销、未知结果不重发 | 缺 Google 应用配置，真实 OAuth/读写未验收 |
| 5 云电脑 | Docker 浏览器镜像、账户持久卷、截图/点击/键入、暂停恢复、主助手观察与逐次操作审批 | Docker daemon 不可用，未构建运行；不是生产 VM/出口隔离方案 |
| 6 个人入口 | 首页对话、账户目标汇总、待审批、连接器和电脑控件、语音输入 | 不是全双工实时语音；完整通知与安静时段未实现 |
| 7 协作 | 主助手接既有持久协作者、报告汇总、个人项目渠道复用主任务、协作者权限隔离 | 真实渠道与多任务端到端自主协作仍待验收 |

## 真实检查

- `.venv/Scripts/python.exe -m pytest -q --basetemp=.scratch/personal-agent/pytest-delivery-12`：137 passed、1 skipped、2 subtests passed，47.91 秒。
- `npm.cmd run build --prefix frontend`：TypeScript 与 Vite 构建通过。
- `npm.cmd test --prefix frontend`：3 passed。
- `.venv/Scripts/python.exe tests/browser_personal.py`：PERSONAL_BROWSER_OK；无个人资料的无头 Edge 验证注册、主助手入口、创建目标、刷新持久化、未配置提示、切换执行明细；未出现 pageerror。截图保存在 browser-check/personal-home.png。
- 新增四个后端模块及对应目标/连接器测试的 Ruff 检查通过；不宣称全仓库 lint 无历史问题。
- `git diff --check` 通过，仅有 Git 行尾转换提示。
- `.venv/Scripts/python.exe tests/live_personal_check.py`：真实 Codex 在隔离账户中，经 Ask 审批成功调用 goal_create 并持久化中文草稿。result.json 为 passed，goal_status 为 draft。任务检查时仍 running；本次不证明任务交付、目标执行或自主完成通过。
- 真实联调早期失败保留在 live-assistant-check/workbench.sqlite3：先发现原生 MCP 审批缺适配，再发现 MCP Python 子进程中文编码损坏。修复后成功；协议测试覆盖审批、未知表单拒绝、中文参数与密钥环境变量过滤。
- 完整回归中的旧共享记忆测试使用了过期 Store 假接口，现改为真实 SQLite/FTS5，验证排序、预算、项目隔离及 pending 排除；没有改动共享记忆实现。
- 测试中沙箱内 asyncio 本机 socketpair 阻塞，获准使用回环连接后运行；没有把卡住的进程记为通过。

## 待完成与环境依赖

Google OAuth/Vault 配置未提供；Docker CLI 无法连接 daemon，标准 Docker Desktop 路径也不存在。尚未执行真实 Google 发信/日历写入、Docker 浏览器登录保留或真实渠道发送。

安装配置见 docs/personal-agent.md。实时语音、通知安静时段、跨项目自动个人记忆和生产级隔离仍是代码缺口，不能只靠配置消除。

未修改宿主 .env、未提交密钥、未部署或创建 Git 提交。
