# NPC 示例对白动态检索设计

- 状态：首版已实现并完成单 seed 真实 A/B；自然度有改善，但事实扩写风险仍需继续调优
- 日期：2026-08-30
- 范围：NPC 最终决定发言后的示例对白检索与 SpeechGeneration 注入
- 不变边界：保留现有消息驱动轮次、ChatDecision、LangGraph 私有记忆召回、可见性、会话草稿和 ExitConsolidation

## 1. 目标

为每个 NPC 提供一组人工编写、审核通过的示例对白。在 NPC 最终决定 `action=speak` 后，系统根据最终 `intent` 自动检索该 NPC 最相关的示例，并把示例注入 `SpeechGeneration`，让模型通过具体示范学习角色的表达方式，减少仅靠抽象人设标签造成的固定口癖、任务摘要式台词和通用 AI 腔。

首版只解决“角色决定说什么以后，怎样找到相似的表达示范”。它不改变角色是否发言、不增加新的模型决策调用，也不让示例对白参与世界事实和私有记忆判断。

## 2. 核心结论

1. 示例对白只在最终 `ChatDecision.action=speak` 后检索。
2. 如果第一次 ChatDecision 返回 `need_memory`，先按现有流程召回私有记忆并再次决策；只有第二次决策最终为 `speak` 才检索示例。
3. 首版以最终 `intent` 作为唯一语义向量查询主体，不把上一轮其他角色的原话拼进同一个检索向量。
4. 当前轮可见消息仍完整进入 SpeechGeneration，用于保证台词回应真实上下文；它们只是不承担首版示例检索职责。
5. `npcId` 是硬过滤条件，只能召回当前 NPC 自己的示例。
6. 每次最多返回 Top 3；示例不足三条时返回全部可用示例。
7. 示例检索与私有记忆检索是两个独立子系统，可以复用 embedding Provider 和基础设施，但不能混用数据、权限语义、缓存或召回结果。
8. 示例检索不增加任何模型调用；检索失败时直接降级为无示例的 SpeechGeneration，不能把本轮发言降级为 `wait`。

## 3. 为什么不照搬记忆召回

私有记忆回答的是“角色是否知道过去发生过什么”。只有当前可见上下文不足时，模型才有权返回 `need_memory`，并在 owner 范围内发起受约束查询。

示例对白回答的是“角色已经决定这样回应时，通常怎样说”。每次发言都可能从具体示范中受益，不存在角色知情范围判断。因此首版不增加 `need_speech_examples`、`exampleQuery` 或“是否检索”的模型分支。

两者的调用关系为：

```text
本轮 triggerMessageIds
        ↓
ChatDecision
  ├─ need_memory
  │      ↓
  │  现有私有记忆召回
  │      ↓
  │  ChatDecision after recall
  │
  ├─ wait / leave_chat ─────────────→ 不检索示例
  │
  └─ 最终 action=speak
         ↓
     SpeechExampleRetriever(final intent, npcId)
         ↓
     SpeechGeneration（可见消息 + 最终 intent + Top 3 示例）
         ↓
     现有校验、稳定排序与发布流程
```

## 4. 首版数据模型

新增独立场景文件：

```text
core/scenario/NPC_SPEECH_EXAMPLES.yaml
```

不把示例直接塞入 `NPC_PERSONAS.yaml`，避免稳定人设定义与可扩充的检索语料耦合。场景文件采用版本化结构：

```yaml
version: 1
status: confirmed

examples:
  - exampleId: npc001_refuse_mediate_01
    npcId: npc_001
    situation: "别人请求她代为向第三方说情"
    intendedMove: "委婉拒绝代为沟通，把行动责任交还给请求者"
    reply: "这话我替你递不合适。你若真有诚意，自己同他说。"
```

字段语义：

- `exampleId`：全局唯一且稳定，用于索引、测试和日志，不提供给模型。
- `npcId`：示例所属 NPC，也是检索的强制过滤条件。
- `situation`：示例适用的抽象情境，不记录具体运行时事件或真实玩家文本。
- `intendedMove`：角色在该情境下准备完成的对话动作和立场。
- `reply`：交给 SpeechGeneration 模仿的人工示范台词。

每名 NPC 首版准备 8～15 条人工固定示例，至少覆盖：寒暄、直接回答、拒绝、不同意、追问、犹豫、缓和冲突、关系紧张、结束话题和无意延伸。首版不从模型历史回复、玩家反馈或生产对话中自动学习示例。

真实模型首轮 A/B 后，林慧兰追加 4 条短句型生活化示例，重点覆盖确认参与、收窄议题、归还当事人选择权和暂缓表态，降低多人讨论中生成会议纪要式台词的倾向。

## 5. 索引文本

每条例子的向量索引文本只由以下字段组成：

```text
情境：{situation}
回应方式：{intendedMove}
```

`reply` 不进入 embedding 文本。这样可避免检索被示例答案中的人物名、地点、话题词或口头禅主导。`reply` 只有在示例已经被召回后才作为 few-shot 示范进入 SpeechGeneration。

例如：

```text
情境：别人请求她代为向第三方说情
回应方式：委婉拒绝代为沟通，把行动责任交还给请求者
```

## 6. 查询构造

首版查询不再调用模型，也不拼接上一轮原始消息，直接使用最终 ChatDecision 中已经结合人设、目标、关系、可见消息和必要记忆形成的 `intent`：

```text
{final_intent}
```

为了保证检索质量，所有 `action=speak` 的 `intent` 必须明确描述：

1. 正在回应谁；
2. 正在回应什么事情或表达；
3. 角色采取的对话动作，例如回答、拒绝、追问、质疑、安慰、妥协或告别；
4. 角色的立场或态度；
5. 必要时说明希望给对方留下的选择空间。

合格示例：

```text
回应玩家请她代为说情的请求；委婉拒绝；让玩家亲自和周慎之沟通；保持长辈式克制，不替玩家作决定。
```

不合格示例：

```text
自然回应玩家。
```

首版通过提示词要求和评测用例保证 intent 信息量，不增加 `speechAct`、`emotionalPosture` 等新协议字段。若后续实测发现不同对话动作的示例混召回，再在第二版加入结构化重排字段。

## 7. 检索算法

首版算法保持简单且可验证：

1. 读取最终 `intent` 并生成查询向量。
2. 强制过滤 `npcId == 当前 NPC`。
3. 对该 NPC 的示例索引文本计算向量相似度。
4. 按相似度从高到低稳定排序；同分时按 `exampleId` 排序。
5. 返回 Top 3；不足三条时返回全部。
6. 对重复 `exampleId` 去重。

首版不进行以下处理：

- 不用当前轮原始消息生成第二个向量；
- 不让模型选择示例；
- 不按关系、情绪或 trigger 进行额外重排；
- 不使用私有 Memory 的正文检索示例；
- 不跨 NPC 召回；
- 不自动把生成结果写回示例库。

示例数量较少时，向量检索仍必须经过与生产 embedding 配置一致的维度校验。若 embedding Provider 不可用、维度不匹配或没有任何该 NPC 示例，则返回空列表并记录可观测错误，不阻断发言。

## 8. SpeechGeneration 注入格式

`_npc_prompt(..., protocol="speech_generation", extra=...)` 的上下文增加只读字段：

```json
{
  "speechExamples": [
    {
      "situation": "别人请求她代为向第三方说情",
      "intendedMove": "委婉拒绝代为沟通，把行动责任交还给请求者",
      "reply": "这话我替你递不合适。你若真有诚意，自己同他说。"
    }
  ]
}
```

不向模型暴露 `exampleId`、向量、相似度、数据库字段或检索日志。

SpeechGeneration 规则增加以下正向约束：

```text
speechExamples 是当前角色在相似情境中的表达示范，只用于学习语气、节奏、措辞密度和处理方式。
结合当前可见消息和本次 intent 重新作答，不得照抄示例中的事实、人物、承诺或完整句子。
当前上下文与示例冲突时，以当前上下文、角色边界和本次 intent 为准。
```

示例只是风格依据，不能成为世界事实证据，也不能生成 Goal、关系、章节效果或记忆变化。

## 9. 与消息驱动轮次的集成

集成点位于一轮的最终 ChatDecision 全部收集并校验之后、并行 SpeechGeneration 之前：

1. 编排器收集所有 NPC 的最终决定。
2. `wait` 和 `leave_chat` 不进入示例检索。
3. 对每个合法 `speak` 决定，使用其 `npcId + final intent` 并行执行示例检索。
4. 把每个 NPC 各自的检索结果放入其 SpeechGeneration prompt。
5. SpeechGeneration 继续按现有 Provider 全局信号量执行。
6. 示例返回顺序确定，不能因并发完成顺序改变最终 prompt。
7. 后续 stale 校验、重复校验、草稿证据绑定、消息稳定排序和发布流程保持不变。

示例检索是本地或数据库读取，不占用模型请求信号量，也不得在等待期间持有 `Run.lock` 或数据库写事务。

## 10. 存储与组件边界

新增独立接口：

```text
SpeechExampleRetriever.search(
    npc_id: str,
    intent: str,
    limit: int = 3,
) -> SpeechExampleSearchResult
```

组件职责：

- 场景加载器：校验 YAML 的版本、唯一 ID、合法 NPC 和非空字段。
- 示例索引器：为 `situation + intendedMove` 生成并维护向量。
- SpeechExampleRetriever：执行 NPC 范围过滤、相似度排序和 Top K 返回。
- 编排器：只决定何时调用检索并把结果传给 SpeechGeneration。
- SpeechGeneration：消费已选择的示例，不负责搜索或修改示例。

实现可以复用现有 embedding Port 和 PostgreSQL/pgvector 连接，但示例表、查询接口和缓存必须独立于 MemoryRetriever。私有记忆的 owner scope、Memory ID 和召回缓存不得扩展为示例检索的通用容器。

## 11. 失败与降级

| 情况 | 首版行为 |
| --- | --- |
| 最终 action 不是 speak | 不执行示例检索 |
| intent 为空或只有空白 | 记录 `speech_example_empty_intent`，无示例生成 |
| 当前 NPC 没有示例 | 返回空列表，无示例生成 |
| embedding Provider 不可用 | 记录错误，返回空列表，无示例生成 |
| 向量维度不匹配 | 拒绝该次检索，返回空列表，不污染索引 |
| 返回重复示例 | 按 `exampleId` 去重后截取 Top 3 |
| SpeechGeneration 失败 | 沿用现有台词生成失败处理，与示例检索无关 |

所有示例检索失败都采用 fail-open：角色仍按现有人设、上下文和 intent 生成台词。它们不能把原本合法的 `speak` 变成 `wait`。

## 12. 可观测性

每次检索记录不含私密正文的结构化指标：

- `runId`、`conversationId`、`roundId`、`npcId`；
- 是否执行检索；
- 查询是否为空；
- 返回数量；
- 返回的 `exampleId`；
- 每条相似度；
- 检索耗时；
- 降级原因。

运行日志和评测报告不能记录私有 Memory 正文。人工固定示例可以在开发评测产物中显示，但生产日志默认只记录 ID 和分数。

## 13. 测试与验收

### 13.1 场景加载

- 五名 NPC 的示例文件可以成功加载。
- 重复 `exampleId`、未知 `npcId`、空 `situation`、空 `intendedMove` 或空 `reply` 必须启动失败。
- 每名 NPC 至少 8 条示例；不足时场景验收失败。

### 13.2 检索单元测试

- 查询只能返回当前 `npcId` 的示例。
- 明确的拒绝 intent 能在 Top 3 中命中拒绝示例。
- 明确的追问、缓和、告别 intent 分别命中对应示例。
- 同分结果按 `exampleId` 稳定排序。
- embedding 不可用、维度错误、空 intent 和无示例时均返回空结果且不抛到轮次顶层。

### 13.3 编排集成测试

- `wait`、`leave_chat` 不调用 SpeechExampleRetriever。
- `need_memory` 阶段不调用示例检索；记忆后二次决定为 `speak` 时只调用一次。
- 同轮多名 NPC 发言时，各自并行检索且不会互相读取示例。
- 示例检索失败不阻止 SpeechGeneration 和其他 NPC 的发言。
- 过期轮次生成结果仍被现有版本检查丢弃。

### 13.4 Prompt 测试

- SpeechGeneration prompt 包含当前 NPC 的 Top 3 示例。
- prompt 不包含示例 ID、相似度和向量。
- prompt 保留当前轮可见消息、最终 intent、persona、关系和已获准记忆。
- 示例中的人物、事件或承诺不能被评测器当作当前世界事实复制。

### 13.5 行为验收

建立覆盖五名 NPC 的固定 A/B 对话集，在相同模型、温度和输入下对比“无示例基线”与“动态示例检索”：

- 人工盲评更偏好动态示例版本；
- 隐去角色名后，角色辨识正确率高于基线；
- 固定口头禅连续重复率不高于基线；
- 任务摘要式、说明书式和通用客服式表达少于基线；
- 事实一致性、直接问题回答率和玩家自主性不得低于现有门禁。

首版上线门禁要求：检索逻辑、范围隔离和降级测试全部通过，且 A/B 行为评测没有出现事实污染或明显自然度回退。

## 14. 首版明确不做

- 不修改模型和微调权重；
- 不自动学习生产回复；
- 不根据玩家点赞、重试或人工评分更新示例；
- 不新增 relationshipTier、speechAct、emotionState 等协议字段；
- 不使用当前轮消息执行第二路场景向量检索；
- 不改变 SpeechGeneration 的温度；
- 不修改现有记忆召回状态机；
- 不允许示例影响 ChatDecision、Goal、关系、章节效果或 ExitConsolidation。

## 15. 后续演进条件

只有首版评测证明确有对应问题时才进入下一版：

- 若相同 intent 在不同场景间混召回，再加入独立的“当前轮消息 ↔ situation”场景相似度，作为低权重重排信号，而不是与 intent 拼成一个向量。
- 若拒绝、追问、安慰等对话动作持续混淆，再给 ChatDecision 增加结构化 `speechAct`。
- 若同一关系阶段的表达差异无法通过 intent 表达，再加入后端计算的关系阶段过滤或重排。
- 若人工审核流程成熟，再设计基于证据、候选、批准和回滚的示例学习机制。
