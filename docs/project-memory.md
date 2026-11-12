# 项目记忆按需加载

账户长期指令、项目约定和现有权限策略继续在任务启动时生效。自主项目记忆只有用户开启后才可访问，历史资料不构成新的指令或授权。

任务启动只加载最近 20 条记忆索引，每条包括 ID、分类、版本和最多 80 字摘要，不加载正文。运行快照保存本轮索引。

- `memory_search(query)`：在当前项目内按正文子串搜索，最多返回 20 条摘要；空查询返回最近索引。
- `memory_read(id, offset=0, limit=2000)`：读取当前项目的一条记忆，单次最多 2000 字符。返回总长度及 next_offset，供继续读取。
- `memory_save` 和已有记忆管理接口保持兼容；无需迁移或删除历史数据。

索引截取正文前 80 字，不额外调用模型生成摘要。大量结果可通过更具体的关键词缩小范围。已有原生会话先前加载的内容不会被本次变更清除，新启动的上下文使用有界索引。

验证：`.venv/Scripts/python -m pytest tests/test_api.py tests/test_bridge.py tests/test_collaboration.py -q --basetemp=.scratch/on-demand-memory/verified`，36 passed。此验证使用确定性执行器边界，不代表真实 Codex/Claude 云端验收。
