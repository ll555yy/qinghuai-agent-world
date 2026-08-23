# Agent 语义评测子智能体审查

- 状态：主会话已完成独立审查与集成修复
- 子智能体：3 个 `gpt-5.6-luna`，reasoning effort `max`
- 网络/费用：三个子智能体均未访问网络、未产生真实费用

## 分工与交付

### semantic_cases_rules

- 文件：`core/evaluation/agent_semantic_cases.yaml`、`models.py`、`case_loader.py`、`rule_scorer.py` 及两个单元测试。
- 原始交付：36 个 Case、Loader、确定性安全硬门和检索指标。
- 主会话复核：修复非法 NPC 错误归一化；修复合法自身 Memory ID 被误判成 owner 泄露；补齐向量、两跳 Graph、跨 owner 高相似、Embedding 降级和空结果；第二轮审计又补齐时间、departed、参与者上限、世界状态、Goal owner 和 Memory 单次调用，最终 47 个 Case。

### llm_judge

- 文件：`judge.py`、`judge_protocols.py`、`judge_calibration_cases.yaml` 及两个单元测试。
- 交付：独立 `doubao-seed-2.1-turbo` Adapter、严格 Schema、六维 Rubric、Candidate 数据定界、格式重试、分歧/低置信度处理、Token/延迟/成本和 Fake Judge。
- 主会话复核：13 个校准 Case，其中 3 个 Injection；统一了重复的 JudgeScore 契约，并把 Judge metrics 接入 Runner/Report。
- 第二轮审计修复：六维 evidence 改为固定必填 Schema；布尔、majorIssues、confidence 改为必填；总分统一为本地六维平均；空/无效 Ark 响应允许一次格式重试；连续 Schema 失败进入人工队列并正确统计。
- 最终只读复审补强：报告契约改为可验证实际输出，新增嵌套 Candidate 摘要内部字段脱敏和产物回读测试。

### evaluation_runner_reports

- 文件：`runner.py`、`report.py`、CLI 及三个测试。
- 交付：dry-run/offline/live、预算/超时/部分报告、JSON/Markdown/Bad Case/人工队列。
- 主会话复核发现并修复：
  1. 初版 live Candidate 自建 Prompt 和温度，已改为复用生产 `DecisionService` 六协议和 `temperature=0.2`。
  2. 初版 Judge 只评每个 Case 的第一条输出，已改为每个 Candidate observation 主评，并稳定抽取至少 20% 二评。
  3. 初版可能跨 observation 比较 Judge 分歧，已改为只比较同一 run 的重复评分。
  4. 初版丢失 Candidate/Judge Token，已增加不改变生产协议的计量包装。
  5. 初版 provider 失败仍可能标记 complete，已改为保存部分报告且 `complete=false`。
  6. 报告指标不完整，已补齐硬门、Memory、P50/P95、Token、成本、Judge 分布/一致性和单独稳定性报告。
  7. live 缺少 Embedding 时曾静默使用 Fake，现改为显式失败，不允许污染真实基线。
  8. Judge 分歧现保留两个原始分数，同时把有效置信度降为 low 并进入人工仲裁。
  9. 离线 Injection 只报告 Prompt boundary，不再伪造模型 Injection 通过率。
  10. 校准 Case 改为严格加载和全 expected 字段对比，区分 prompt-only/skipped/live-scored，并把校准预算、超时和 stop reason 合并到 execution。
  11. 费用门改为调用前预留最坏单次成本；主会话再补齐 provider retry 的真实请求计数。

## 主会话最终集成修复

- 根据火山方舟实测将独立 Judge 改为 Responses API；Candidate 生产 Chat Completions 路径保持不变。
- 使用 `store=false`、关闭 thinking 和原生严格 JSON Schema；去掉 Prompt 中重复 Schema，缩短证据输出以控制授权费用。
- 修复 `MajorIssue` 已知字符串的严格枚举解析、`major_issues=["none"]` 误入人工队列、失败响应 Token/费用丢失和 Candidate/Judge 费率混用。
- 补齐 `rules_010` chapterEffects/agenda/world mutation 硬门，以及缺少 trusted scope 时 actor/goal/memory fail closed；Candidate 自带 allowlist 不能扩权。
- 新增只续跑缺失 Judge 校准 Case 的显式授权脚本，最终在累计 CNY 1 上限内完成 13/13 真实校准。

## 文件边界

未修改七日可达性 Goal 的 simulation、evidence、脚本或结果文档；只按用户明确授权整理三个七日测试文件的 import 顺序，未改测试语义。`.env.example` 只新增 Judge 的非敏感模型/地址示例；Candidate 模型和生产启动路径未改。
