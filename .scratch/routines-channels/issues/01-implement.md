Status: ready-for-agent

# 实现自动化、渠道与插件包

完成规格、持久化 API、页面、回归和浏览器验证。

## Answer

已完成本地每日/每周计划、手动执行、启停与历史记录；后台调度与渠道回传分别运行。Slack Socket Mode 与 Telegram getUpdates 支持指定聊天/用户、幂等接收、线程续聊、手动交接和完成回传。发送失败与结果不明分别记录，重启不自动重放不明操作。插件 JSON 包复用 Skill/stdio MCP 校验，默认停用并支持整体启停。

本地验证：
- `.venv/Scripts/python.exe -m pytest -q --basetemp .data/test-routines-release-20261107`：92 passed、1 skipped、2 subtests passed。
- `npm.cmd test --prefix frontend`：3 passed。
- `npm.cmd run build --prefix frontend`：TypeScript/Vite 构建通过。
- `.venv/Scripts/python.exe -m ruff check backend/workbench/automations.py tests/test_automations.py`：通过。
- `.venv/Scripts/python.exe tmp/run_automation_browser.py`：独立禁用执行器服务 + 无头 Edge 验证计划创建/立即执行/启停、执行记录打开任务、两种渠道配置、身份验证失败提示、刷新恢复和插件包启停；最终版本通过。截图 output/automations-browser.png。
- 原本地服务已在确认 active_runs=0 后更新；http://127.0.0.1:8000/ 返回 200，health 返回 ok 且 automation_error 为空，OpenAPI 包含 routines/connections/plugins 路径。

指南：docs/automations-and-channels.md。没有使用真实 Slack/Telegram 密钥或发送外部消息；渠道验收是离线运输/HTTP替身，不能视作真实平台验收。没有进行定时真实模型交付验收，也没有部署云端。新连接默认停用，用户需配置环境变量、允许用户和收发权限后启用。
