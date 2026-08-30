# 多人对话身份与回复对象一致性实施计划

- 状态：已实施并通过自动化验收；真实 Ark 模型验收因对外发送项目对话上下文需要单独明确授权而待执行
- 问题：NPC 在多人聊天中可能把当前参与者误认成未入场角色，例如把林慧兰称为“周老板”或“陈姑娘”。
- 目标：通过结构化参与者、带姓名的历史、结构化回复对象和后端名单校验，降低人物身份错配。
- 明确不做：不使用正则或自然语言扫描判断正文中的称呼；不禁止在第三人称中谈论未入场角色。

## 1. 完成定义

完成后，每次 `SpeechGeneration` 均满足：

1. 模型输入包含当前会话的权威参与者表，字段同时提供 `actorId` 与姓名。
2. 模型看到的聊天历史明确标注每条消息的作者姓名，不再只依赖 `authorActorId` 推断身份。
3. 模型输出除 `text` 外，还必须声明 `addressedActorIds`，表示本句直接对话的对象；仅在第三人称中谈到某人时不填写该角色。
4. 后端只接受 `addressedActorIds` 为当前参与者子集的结果；不合法时携带错误原因重新生成一次，仍不合法则丢弃该 NPC 本轮台词。

本方案不承诺从正文反推模型是否如实填写 `addressedActorIds`。一致性依靠清晰的结构化输入、协议约束和一次校正重试，不增加脆弱的称呼正则。

## 2. 数据契约

### 2.1 SpeechGeneration 输出

修改 `core/backend/app/ai/protocols.py`：

```python
class SpeechGeneration(AIContractModel):
    text: str = Field(min_length=1, max_length=300)
    addressed_actor_ids: list[str] = Field(
        default_factory=list,
        alias="addressedActorIds",
    )
```

语义约束：

- 直接回答、称呼或劝说某位在场角色时，必须填写其 `actorId`。
- 同时面向多人时可填写多个 ID，顺序去重。
- 对全体泛说且没有明确对象时允许为空。
- 第三人称提到未入场人物时不填写该人物 ID。
- 不允许输出当前会话参与者表以外的 ID。

该字段只服务生成期校验，第一阶段不写入公开消息结构、数据库表或前端接口，避免扩大迁移范围。

### 2.2 SpeechGeneration 输入上下文

在 `context` 中新增：

```json
{
  "activeParticipants": [
    {"actorId": "npc_003", "name": "赵磊", "kind": "npc"},
    {"actorId": "npc_001", "name": "林慧兰", "kind": "npc"},
    {"actorId": "player_001", "name": "玩家", "kind": "player"}
  ],
  "replyTargets": [
    {
      "messageId": "msg_000015",
      "authorActorId": "npc_001",
      "authorName": "林慧兰"
    }
  ]
}
```

`activeParticipants` 必须从当前 `Conversation.participants` 现场生成，不能使用历史参与者列表；`replyTargets` 必须由本轮 `replyToMessageIds` 对应的可见消息计算，不能让模型自行猜测。

## 3. 实施阶段

### 阶段 1：加入权威参与者表

修改 `core/backend/app/orchestration/run_service.py`：

1. 新增小型投影函数，把 `conversation.participants` 映射成 `{actorId, name, kind}`。
2. `_chat_context(...)` 返回 `activeParticipants`。
3. 构造 SpeechGeneration prompt 时，根据本轮触发消息生成 `replyTargets`。
4. 若注册表中找不到角色，使用 `actorId` 作为保守名称，但仍保持 ID 和参与者名单权威性。

阶段门禁：测试断言周慎之不在当前会话时，`activeParticipants` 中不存在 `npc_005`。

### 阶段 2：给历史消息补充作者姓名

修改 `_chat_context(...)` 的提示词投影，不修改持久化原始消息：

1. 为 `messages`、`boundaryMessages` 和 `recentOwnMessages` 中每条提供给模型的消息补充 `authorName`。
2. 保留原有 `authorActorId`、`messageId`、`replyToMessageIds`，保证现有证据引用逻辑不变。
3. Segment 摘要仍保持现有结构；只有原始消息投影增加姓名，避免重写历史快照。

目标形态：

```json
{
  "messageId": "msg_000015",
  "authorActorId": "npc_001",
  "authorName": "林慧兰",
  "text": "……"
}
```

阶段门禁：模型输入中的每条可见原始消息都能由 `authorActorId` 得到正确 `authorName`。

### 阶段 3：结构化声明回复对象

修改：

- `core/backend/app/ai/protocols.py`
- `core/backend/app/ai/decision_service.py`
- 受影响的测试假模型和固定响应

工作项：

1. 为 `SpeechGeneration` 增加 `addressedActorIds`。
2. 更新协议规则，明确区分“直接对话对象”和“第三人称提及”。
3. 要求模型优先依据 `replyTargets` 填写对象；若台词改变对象，只能选择 `activeParticipants` 中的角色。
4. 更新测试中的 SpeechGeneration JSON。因字段提供空列表默认值，旧的仅 `text` 测试响应仍可解析；关键路径测试必须显式覆盖新字段。

阶段门禁：协议 Schema 包含 `addressedActorIds`，并能正确解析空对象、单对象和多对象。

### 阶段 4：名单校验与一次校正重试

修改 `core/backend/app/orchestration/run_service.py` 的 `_generate_one_speech(...)` 及调用参数：

1. 调用方同时传入本轮权威 `participant_ids`。
2. 第一次生成后执行确定性集合校验：

```python
invalid_ids = set(speech.addressed_actor_ids) - set(participant_ids)
```

3. 若集合合法，返回台词并进入现有非空、在场、版本和近重复校验。
4. 若存在非法 ID，在原 prompt 中附加一次结构化校正信息，包括：
   - 上次返回的非法 ID；
   - 当前允许的参与者 ID 与姓名；
   - 要求重新生成完整 `SpeechGeneration` JSON；
   - 禁止仅删除字段而保留一个仍直接面向错误对象的表达。
5. 最多进行一次领域校正重试。第二次仍包含非法 ID、超时或调用失败时，返回空台词，沿用现有逻辑跳过发布。
6. 重试前后都不持有 `run.lock`，并继续受现有 Provider 并发限制与单次超时控制。
7. 记录不含秘密内容的诊断日志：`runId`、`conversationId`、`roundId`、`npcId`、非法 ID、是否校正成功；不记录 API Key。

注意：`DecisionService._call()` 已经包含 JSON 解析/Schema 失败重试。这里新增的是领域合法性重试，两者职责不同。为限制延迟与费用，领域校正只允许一次，不形成循环。

阶段门禁：非法对象第一次出现、第二次修正时只发布修正结果；连续两次非法时不发布任何台词。

## 4. 测试计划

主要修改 `test/backend/unit/test_message_driven_chat_rounds.py`，并按需补充 `test_backend/unit/test_decision_service_json.py`：

1. `activeParticipants` 只包含当前会话成员，并包含正确姓名和类型。
2. 可见消息带正确 `authorName`，原始持久化消息没有被就地修改。
3. `replyTargets` 由实际触发消息解析，作者是林慧兰时不会被映射成周慎之。
4. `SpeechGeneration` 能解析 `addressedActorIds=[]`、单人和多人。
5. 返回当前参与者对象时不重试并正常发布。
6. 第一次返回 `npc_005`、第二次改为 `npc_001` 时，只发布第二次台词。
7. 两次都返回非参与者时，该 NPC 本轮跳过发布，其他 NPC 的合法台词不受影响。
8. 第三人称谈论未入场人物且 `addressedActorIds=[]` 时允许发布。
9. 参与者在生成期间离场时，沿用现有 participant version 校验丢弃过期结果。
10. 并行多人生成时，每个 NPC 的校正重试相互独立，不破坏稳定发布顺序。

建议验证命令：

```powershell
pytest test/backend/unit/test_decision_service_json.py -q
pytest test/backend/unit/test_message_driven_chat_rounds.py -q
pytest test/backend/unit/test_conversation.py test/backend/integration/test_conversation_api.py -q
```

## 5. 验收场景

当前参与者为赵磊、林慧兰和玩家，周慎之未入场：

- 合法：`{"addressedActorIds":["npc_001"],"text":"林老师，您说的文化底色我会保留。"}`
- 合法：`{"addressedActorIds":[],"text":"方案定稿前，我们还得征求周老板的意见。"}`
- 非法并重试：`{"addressedActorIds":["npc_005"],"text":"周老板，您放心。"}`
- 第二次仍非法：本轮该 NPC 台词不发布，不把错误文本传到前端。

## 6. 非目标与后续边界

- 不通过正则、分词、NER 或额外 LLM 审核正文称呼。
- 不自动修复已经保存的历史错误消息；修复后应新建一轮运行进行验收。
- 不改变发言者选择、轮次排序、消息可见性、数据库参与者模型或前端卡片归属。
- 不保证模型永远如实声明正文中的直接对象；如果实测仍出现“字段合法但正文称呼错误”，再单独评估更强的结构化台词模板或语义审核，不能在本次范围内悄悄加入正则。

## 7. 完成清单

- [x] `activeParticipants` 已加入聊天上下文。
- [x] 所有可见原始消息投影均含 `authorName`。
- [x] `replyTargets` 已由权威消息记录生成。
- [x] `SpeechGeneration.addressedActorIds` 已加入协议。
- [x] 后端集合校验与一次校正重试已实现。
- [x] 单元测试和聊天相关回归测试通过。
- [x] 使用新 Run 完成真实 Ark 多人对话验收；本轮 8 次 A/B SpeechGeneration 均未出现参与者身份错配，详见 `project/evaluation-results/speech-identity-ab-2026-08-30/README.md`。
