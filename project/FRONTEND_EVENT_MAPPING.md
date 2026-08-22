# 前端 REST 与事件映射

> 权威来源：`core/backend/app/api/routes`、`Run.to_public_snapshot()` 和 `RunService.append_event()` 调用。
> 原则：前端只消费公开投影，不访问数据库或场景 YAML 私有字段。

## 1. REST 映射

| 前端行为 | 方法与路径 | 请求 | 主要响应/用途 |
|---|---|---|---|
| 健康检查 | `GET /api/health` | 无 | 后端、场景和数据库可用状态 |
| 开局读取任务 | `GET /api/scenario/agendas` | 无 | 无需创建 Run 的章节信息、五项公开主张和五名 NPC 公开资料 |
| 创建旁观 Run | `POST /api/runs` | `{agendaId:null}` | 初始公开 `RunSnapshot` |
| 创建任务 Run | `POST /api/runs` | `{agendaId,seed?}` | 初始公开 `RunSnapshot` |
| 恢复 Run | `GET /api/runs/{runId}` | 无 | 完整公开快照 |
| 推进世界 | `POST /api/runs/{runId}/world/step` | `{realSeconds:2,commandId}` | 新时间和快照 |
| 查看 NPC | `GET /api/runs/{runId}/actors/{actorId}` | 无 | 公开人物卡、状态、坐标 |
| Run 内重读任务 | `GET /api/runs/{runId}/agendas` | 无 | 五项公开主张 |
| 补拉事件 | `GET /api/runs/{runId}/events?afterSeq=N` | 无 | 重连事件 |
| 玩家邀请 NPC | `POST /api/runs/{runId}/invitations` | `{targetActorId,commandId}` | 邀请结果和快照 |
| 回应 NPC 邀请 | `POST /api/runs/{runId}/invitations/{id}/respond` | `{accepted,commandId}` | 邀请/会话结果 |
| 玩家申请加入 | `POST /api/runs/{runId}/conversations/{id}/join` | `{commandId}` | 加入请求；成功时含历史消息 |
| 回应 NPC 加入 | `POST /api/runs/{runId}/join-requests/{id}/respond` | `{accepted,commandId}` | 加入请求结果 |
| 查询加入请求 | `GET /api/runs/{runId}/join-requests/{id}` | 无 | 请求最新状态 |
| 获取历史 | `GET /api/runs/{runId}/conversations/{id}/messages` | 无 | 玩家曾参与会话的公开历史 |
| 玩家发言 | `POST /api/runs/{runId}/conversations/{id}/messages` | `{text,commandId}` | 新会话状态和快照 |
| 玩家离开 | `DELETE /api/runs/{runId}/conversations/{id}/participants/player_001` | `{commandId}` | 会话和快照 |

`commandId` 由前端为每次用户命令生成 UUID。同一命令重试必须复用原 UUID，新操作必须新建 UUID。

## 2. WebSocket

连接：`/ws/runs/{runId}?afterSeq=N`。

若携带 `afterSeq`，服务器会先重放缺失事件，再发送公开快照；无 `afterSeq` 时直接发送快照。客户端通过是否具有 `eventType` 区分事件与快照，并按 `eventSeq` 去重。快照总是可以替换公开服务器状态，UI 临时状态不随快照清空。

## 3. 事件映射

| `eventType` | 关键 payload | 前端处理 |
|---|---|---|
| `run_created` | worldTime、playerAgendaId | 进入世界并初始化顶栏 |
| `time_advanced` / `world_stepped` | worldTime | 更新时间；检查 17:00/17:50/18:00 UI |
| `world_day_started` | worldTime | 关闭日终遮罩，展示新 Day |
| `world_day_ended` | worldTime、reason | 打开日终遮罩并关闭输入 |
| `world_event_occurred` | event、worldTime | 横幅、事件记录、场景轻提示 |
| `npc_thought_started` | actorId、worldTime | Actor 状态标记为“思考中”表现 |
| `npc_thought_skipped` | actorId、reason | 清理思考表现，不暴露内部原因细节 |
| `npc_waited` | actorId、reason? | Actor 恢复等待表现 |
| `actor_movement_started` | actorId、targetActorId | Phaser 启动靠近动画 |
| `actor_movement_completed` | actorId、position | 提交权威坐标并结束动画 |
| `invitation_requested` | invitationId、双方 actorId | 显示请求气泡；目标为玩家时弹响应卡 |
| `invitation_request_cleared` | invitationId、reason? | 移除请求卡和等待气泡 |
| `invitation_accepted` | invitationId、conversationId | 成功提示，等待/应用会话事件 |
| `invitation_refused` | invitationId、targetActorId | 显示拒绝气泡和通知 |
| `invitation_expired` | invitationId、expiredAt | 显示“已过可邀请时间” |
| `conversation_created` | conversation | 新建/替换会话公开状态，绘制聊天圈 |
| `conversation_participant_joined` | conversation、actorJoined | 更新成员并插入系统消息 |
| `conversation_participant_left` | conversation、actorLeft | 更新成员并插入系统消息 |
| `conversation_closed` | conversation | 关闭输入，显示 closeReason |
| `message_created` | conversationId、messageId、authorActorId、text | 追加公开消息；没有 text 时不得生成台词 |
| `conversation_activity` | conversationId、reason | 显示非文本活动状态 |
| `conversation_idle` | conversationId、idleCount | 显示短暂静默状态；不由前端计轮次 |
| `join_request_created` | joinRequest | 绘制加入请求；玩家是审批人时弹响应卡 |
| `join_request_resolved` | requestId、status 等 | 清除请求并展示接受/拒绝/过期 |
| `npc_consolidated` | conversationId、npcId、status | 只显示必要的“角色整理完这次谈话”，不展示 Memory |
| `chapter_resolved` | branch、agendaResults、playerTaskResult、actorStances、playerHighlights | 停止计时与命令，进入结局页 |

## 4. 公开数据模型

前端生成或维护的消费类型至少包括：

- `WorldTime`：day、hour、minute、time、label、status。
- `PublicActor`：actorId、kind、name、role、publicBackground、publicImpression。
- `ActorState`：status、position。
- `PublicConversation`：conversationId、creationSeq、participants、status、closeReason?。
- `PublicMessage`：messageId、conversationId、authorActorId、text、createdAt、segmentId。
- `PublicWorldEvent`：eventId、worldDay、at、visibility、sourceLabel、summary。
- `ChapterResolution`：chapterId、branch、agendaResults、playerTaskResult、actorStances、playerHighlights。
- `RunSnapshot`：公开快照字段的组合，含仅与玩家相关的 `pendingInvitations` 和 `pendingJoinRequests`，用于正常重连恢复。
- `RunEvent`：runId、eventSeq、stateVersion、eventType、payload。

OpenAPI 可生成 REST 请求基础类型，但当前许多响应声明为 `dict[str, Any]`，无法生成精确快照类型。第一版在前端为公开响应建立窄类型和运行时类型守卫；这只是消费端校验，不改变业务规则。后续可把这些公开投影升级为 Pydantic Response Models。

## 5. 错误映射

重点错误码：`run_not_found`、`actor_not_found`、`conversation_full`、`conversation_limit_reached`、`actor_already_in_conversation`、`invalid_invitation`、`invalid_join_request`、`player_access_denied`、`chapter_already_ended`、`duplicate_command`、`invalid_world_step`。

前端显示简洁中文，调试区域保留 code；未知错误统一为“操作未完成，请重新同步后再试”，不能假装成功。
