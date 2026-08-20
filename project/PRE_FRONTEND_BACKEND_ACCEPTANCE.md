# 前端开发前后端验收报告

状态：执行中，前端门禁尚未解除。

## 已通过

- 方舟 `doubao-seed-2.0-lite` 六协议真实调用全部首轮 Schema 成功。
- Agent Plan `doubao-embedding-vision-251215` 真实返回 2048 维。
- 开发库和测试库已迁移到 `vector(2048)`，使用 `halfvec(2048)` HNSW 表达式索引；Alembic check 无漂移。
- 指定 Run 的 36 条私有 Memory 已在用户授权下完成真实回填；重跑全部跳过，Repository 重开后语义召回仍生效，owner 越界为 0。
- 方舟私有 Memory 批次经实测固定为 8；36 条单批失败不会修改权威 Memory，8 条分批全部成功。
- 25 条有向初始关系均有 owner 私有 `scenario_seed` Memory，并由加载器强制校验。
- 普通 pytest 强制内存仓储和空 Key，不能因本机 `.env` 产生真实请求。

## 未通过

- 至少一次真实 NPC 聊天、离场沉淀与 Repository 重启恢复尚未执行；验收脚本会新建临时 Run，仍需用户将这类新建 Run 明确纳入外发授权。
- `observer`、`pro_lin`、`pro_zhao` 三局真实七日模拟尚未执行。
- 基于三局数据的 Prompt/玩法参数调整和受影响路线复跑尚未执行。

这些项目完成前不能宣称真实模型下的后端可玩性完成，也不能开始正式前端开发。
