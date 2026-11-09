Status: ready-for-agent

# CSV 上传、交互、版本与重启恢复

实现规格中的闭环并验证 API 和浏览器。

## Answer

已实现 CSV 上传及工作区源文件、SQLite 数据快照、图表版本事务、对话记录和事件。前端提供图表/筛选/数据表/历史版本/重开恢复。真实执行器通过 chart_list / chart_revise 修改当前任务图表，成员不继承工具；不配置执行器时明确报错。

验证命令与结果：
- `.venv/Scripts/python.exe -m pytest -q --basetemp .data/test-all-charts-20261107`：75 passed、1 skipped、2 subtests passed。
- `npm.cmd test --prefix frontend`：3 passed。
- `npm.cmd run build --prefix frontend`：TypeScript 与 Vite 构建通过。
- `.venv/Scripts/python.exe tmp/run_chart_browser.py`：独立测试服务 + 无头 Edge 验证上传、字段筛选、图表切换、保存版本、历史查看、关闭重开、数据表、错误 CSV 和执行器不可用提示，最终版本复测通过；截图 output/csv-chart-browser.png。

边界：图表展示前 100 行、不聚合；保存后才恢复筛选；未调用真实模型做端到端分析/自然语言修改验收。云端部署、任意 HTML、浏览器操作和应用连接不在本次范围。
