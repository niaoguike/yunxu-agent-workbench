Status: resolved
Type: task
Blocked by: none

# 对齐原生 Auto 工具授权

用户确认减少逐次工具授权，关键业务确认保持独立。

## Answer

任务保存 Auto/Ask 并写入运行快照。Codex 对齐参考实现的 on-request + workspace-write + auto_review，Ask 显式指定 user，恢复线程与单轮都覆盖旧 reviewer；未知协议回退人工。Claude 对已知原生工具使用 SDK Auto，受保护/项目外路径及未知/MCP 工具保留人工门。没有提供 Full Access。

37 tests passed，1 个符号链接测试因 Windows 不允许创建链接而 skipped，2 subtests passed；浏览器默认 Auto、切换 Ask 与持久化验证通过。真实 Codex 在独立工作区创建和读取文件、completed，人工授权数 0。Claude 带凭据 Auto 和危险操作原生拒绝链尚未实测，未扩大此票验证结论。

服务在确认无运行任务后更新，原项目及成果保留。仅切换工具权限不重置需求阶段和待验收确认。详细映射、来源与边界见 docs/adr/0004-native-auto-permissions.md。
