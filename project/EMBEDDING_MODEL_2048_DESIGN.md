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

## 私有 Memory 真实验收

用户明确授权后，对单一指定 Run 完成了定向验收，报告和终端均未输出 Memory 正文：

- 36 条 Memory 全部写入真实向量，数据库核验为 `36/36`、统一 2048 维、配置模型 `doubao-embedding-vision`。
- 第二次运行得到 `indexed=0 skipped=36 failed=0`，证明回填幂等。
- 仓储关闭并重新创建后，使用与原文关键词不同的中文问题进行检索，目标关系 Memory 进入前 8，向量命中 8 条。
- 检索 owner 固定为 `npc_001`，返回结果的 owner 越界数为 0；Graph 查询实现仍在每一跳重复 owner 条件。
- 36 条一次性提交会失败；改为每批 8 条后 36 条全部成功。因此项目的索引器、回填脚本、真实聊天和七日模拟统一采用 8 条作为已验证的方舟安全批次。失败批次没有损坏 Memory 或留下部分向量。

尚未把“高关键词但语义无关”的独立对照样本写入权威场景 Run；该排序性质已有 Fake Embedding 自动测试覆盖，后续真实模拟报告继续观察实际混合排序。
