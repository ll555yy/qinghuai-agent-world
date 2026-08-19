# 方舟 Embedding 模型与 2048 维设计

- 验证日期：2026-08-20
- 配置模型：`doubao-embedding-vision`
- 实际模型：`doubao-embedding-vision-251215`
- 有效 Base URL：`https://ark.cn-beijing.volces.com/api/plan/v3`
- 实际输出维度：2048

## 真实探测

Coding Plan `/api/coding/v3` 对当前 Agent Plan Key 返回 HTTP 401，未作为项目地址。Agent Plan `/api/plan/v3` 请求成功：两条固定公开文本返回两个 2048 维向量，57 Token；迁移完成后的复验耗时 1010 ms，无错误。

## 存储与索引

- PostgreSQL 使用 `vector(2048)` 保存模型返回的完整 float32 向量，不截断、不补零。
- pgvector 的 HNSW `vector` opclass 最多索引 2000 维，不能直接索引本模型。
- 项目使用 `embedding::halfvec(2048)` 表达式建立 `halfvec_cosine_ops` HNSW，并在查询中使用同一表达式；数据库内仍保留完整 float32 向量。
- Alembic revision：`a07d8e9f0123`。迁移只清理可重建的旧向量字段，不删除 Memory 权威文本。
- 模型名或维度改变时必须再建 migration 并全量回填，禁止静默兼容。

## 失败边界

适配器拒绝空批次、超过 256 条的批次、错误维度、NaN 和 Infinity。远程失败时 Memory 文本先保存在数据库，向量保持 `NULL`，检索退回关键词与 Graph。

## 待验证

真实向量回填会把 NPC 私有 Memory 文本发送给火山方舟，必须取得用户明确授权。授权后需要验证：幂等回填、重启持久化、同义语义召回、关键词干扰、owner 预过滤和 Graph 每跳 owner 隔离。
