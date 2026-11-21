Status: ready-for-human
Type: task

# 04 Google 邮件与日历真实联调

代码与协议测试已实现 OAuth state/PKCE、加密存储、增量游标、显式订阅、审批发送及撤销。提供方应用配置缺失，不能记为真实联调通过。

需要管理员在部署环境设置 WORKBENCH_VAULT_KEY、WORKBENCH_GOOGLE_CLIENT_ID、WORKBENCH_GOOGLE_CLIENT_SECRET、WORKBENCH_GOOGLE_REDIRECT_URI，并在 Google 控制台启用 Gmail/Calendar API。密钥不要发送到聊天或提交仓库。

## Answer
协议夹具验证通过；真实 OAuth、邮件及日历读写待账户授权后验证。使用说明见 docs/personal-agent.md。
