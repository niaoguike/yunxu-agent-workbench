# 聊天历史混合检索

GET /api/projects/{project_id}/history/search?q=查询&limit=20

需要登录且拥有项目。返回 results（消息内容、任务 ID、标题、RRF score）、mode、warning。
FTS5 trigram + BM25；配置 VOYAGE_API_KEY 后使用 voyage-3.5-lite 512 维向量，通过 sqlite-vec cosine 距离召回，RRF k=60。首次查询回填该项目历史向量，后续复用内容匹配的缓存。无密钥或语义服务失败时退化关键词检索。

限制：trigram 关键词需至少三个字符；首次语义查询同步回填，历史较多时延迟较高；当前交付为 API，无新增前端入口。缓存内容会保存在同一 SQLite 中。

验证：直接执行 test_history_scope_and_sync 通过；compileall 通过。pytest 未安装，sqlite-vec 安装因网络受限失败，真实 Voyage 与向量分支未验证。
