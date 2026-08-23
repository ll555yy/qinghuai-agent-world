# Prompt 与玩法参数调优记录

状态：Day1 与旧版 observer/pro_lin/pro_zhao 单目标基线已完成验收；本阶段改为验证多 NPC 玩家影响与结局可达性，参数和真实结果待 3-seed 批次确认。

| 轮次 | 路线/seed | 数据问题 | 修改 | 复跑结果 | 结论 |
|---|---|---|---|---|---|
| Day1 闭环基线 | seed 20260820 | 两场聊天产生 17 条消息、90 次逻辑调用、94 次物理请求，耗时 876926 ms；单个触发最多连续 8 轮，七日批量在原 600 秒上限内不可行 | 普通消息/开场触发的连续回复深度由 8 调为 2；参与者变化后的追加深度由 7 调为 1；单局仍保留消息数、调用数和总时限硬上限 | 待三局七日报告 | 暂时保留，三局后复核发言不足与结局可达性 |
| observer 首次真实七日 | seed 20260819 | Day3 18:00 在合法 Goal `achieved` 写入时触发 PostgreSQL `IntegrityError`；运行时、Schema、场景与设计均使用 `achieved`，但数据库旧约束仍只接受 `completed` | 新 migration 将历史 `completed` 转为 `achieved`、误用的 Goal `departed` 转为 `abandoned`，数据库约束与四态权威合同对齐；resolved 时间判断同步改为 `achieved` | 待 observer 重跑 | 数据契约修复，非 Prompt 调整；必须保留 |
| observer Day7 基线 | seed 20260819 | 成功到 Day7 18:00，188 次物理请求、14 场聊天、40 条 NPC 台词；但 42 次 ChatDecision 均未召回 Memory，章节 stance 变化为 0，且对话集中在 npc_001↔npc_004 与 npc_002↔npc_005 | 初始会话缓存由 8 条降为 4 条；ChatDecision 明确区分“缓存已足够”与“旧事/承诺/关系成因/Goal 历史缺证据时必须召回”；明确已有本人表态时生成 chapterEffects；DailyAction 加入 priorConversationCounts 并在人设/Goal 允许时降低重复找同一对象的倾向 | 待三路线复跑 | 暂时保留；以真实召回、目标多样性和 stance 指标决定最终参数 |
| observer Day7 调优一 | seed 20260819 | 到达 Day7 18:00，215 次物理请求、13 场聊天、43 条 NPC 台词、29 次离场沉淀、20 条新聊天 Memory、9 次 Goal 变化、20 组关系变化，Repository 恢复成功；全员均有主动行动和发言，对话对象明显更多样。但仍为 0 次 Memory 召回、0 次章节立场变化，结局为 `no_submission`；Schema 总成功率 83.66%，期间有 5 次 DailyAction 超时和若干 Embedding 批次暂时失败 | DailyAction 在当天事件让未解旧事/承诺/关系成因成为真实 Goal 障碍时，可自然邀请相关人核实并把线索写入 intent；SpeechGeneration 遇到这种 intent 必须提出具体问题。发现 ChatDecision/ExitConsolidation 提示词未提供合法 Agenda ID 和公开说明，新增仅属于当前 NPC 的 `chapterContext`（五项公开 Agenda、自己的整体/分项立场、周慎之自己的授权状态），ExitConsolidation 在本人台词证据明确时必须生成 chapterEffects | 待 observer/pro_lin/pro_zhao 复跑 | 保留输入补全；不硬编码任何 NPC 选择、立场或结局。Embedding 网络失败仍按“Memory 文本先落库、向量待补”正常降级 |
| observer 章节上下文首次复跑 | seed 20260819 | 运行到 Day5 18:00 后出现 `IntegrityError`，此前已完成 10 场聊天、37 条 NPC 台词和 177 次物理请求；临时 Run 正常清理。真实模型可能在同一 Memory 的 `evidenceMessageIds` 中重复一个消息 ID，运行时可见性校验通过，但规范化证据表的复合主键不允许重复，Fake Model 未覆盖该形状 | MemoryExtraction 入库时保持顺序去重 evidenceMessageIds；规范化投影再次按 `(memoryId, messageId)` 去重；新增真实 PostgreSQL 回归测试。模拟报告今后只补充安全的数据库 constraint name，不输出 SQL 或数据值 | 单元 22 项、PostgreSQL 定向回归 1 项和 mypy 均通过；待真实路线复跑 | 保留；属于真实输出驱动的数据规范化修复，不改变 NPC 决策 |
| 召回触发可观测性 | observer 前两轮 | 即使初始缓存降为 4，旁观路线到 Day5/Day7 仍为 0 次召回；报告无法区分“没有人提到历史”与“提到了但 Agent 认为无需检索”。Day5 古籍事件原在 11:00，已有部分 NPC 完成当天唯一一次思考 | 报告只新增历史线索邀请/消息数量，不保存正文；ChatDecision 明确 persona/coreSecrets 不能替代具体事件/承诺/关系成因的 Memory 证据；Day5 古籍受潮提前到 09:00，让所有错峰思考槽位都能看到当天事件 | 待真实三路线复跑 | 暂时保留；若仍无历史线索则调整事件/行动提示，若有线索无召回则调整 ChatDecision，不盲目提高全局召回率 |
| observer 证据去重后完整复跑 | seed 20260819 | 正常到 Day7 18:00，202 次物理请求、15 场聊天、49 条消息、12 次 Goal 变化、20 组关系变化、Repository 恢复成功，Schema 总/首次成功率均约 99.5%；出现 5 个历史线索邀请 intent 和 3 条历史话题消息，但 Memory 工具仍为 0 次。说明触发话题已存在，4 条会话初始缓存已直接覆盖旧事证据 | `INITIAL_MEMORY_CACHE_LIMIT` 从 4 收紧为 1；当天 freshEvent 仍优先进入缓存，其他参与者关系、旧承诺和历史事件由 ChatDecision 需要时通过 owner-safe 工具召回 | 待 observer/pro_lin/pro_zhao 复跑 | 保留；这是“缓存足够所以不调用工具”的数据驱动修正，不通过系统强制伪造召回 |
| observer 缓存 1 正式验收 | seed 20260819 | 正常到 Day7 18:00，14 场聊天、45 条消息、229 次物理请求；发生 2 次按需召回，返回 16 个 owner-safe ID，16 次真实向量命中、0 次 Graph 扩展命中；9 次 Goal 变化、18 组关系变化、Repository 恢复成功，临时 Run 删除。方舟后期抖动造成 20 次 provider retry，Schema 总成功率仍约 95.29%。旁观路线 0 章节立场变化，结局 `no_submission` | 不再调整召回缓存；Graph 0 命中表示向量种子已直接覆盖查询，不视为失败。把章节立场与玩家影响留给 pro_lin/pro_zhao 验证 | 全部真实质量门通过 | 保留缓存上限 1；召回低频且真实生效，没有每消息检索 |
| pro_lin 首次完整路线 | seed 20260819 | 到达 Day7 18:00，玩家邀请与 1 条支持消息均成功；2 次召回、16 次向量命中、13 场聊天、37 条消息、173 次物理请求，但 0 个章节立场变化，玩家结果 `failed`。原通用质量门错误地把该样本判为通过 | ChatDecision 明确“本次 speak 将表达立场时，chapterEffects 可暂留空 evidenceMessageIds；后端只在台词真实生成后绑定新消息证据”。支持路线质量门新增玩家消息、至少 1 个章节立场变化和玩家结果要求；增加显式章节效果真实小探针 | 小探针通过：1 条真实 NPC 台词、章节效果提交、12 次物理请求、0 重试、临时 Run 删除 | 保留；修复的是已实现但未告知模型的正式协议，不硬编码立场值或结局 |
| pro_lin 单次显式立场询问 | seed 20260819 | 到达 Day7 18:00，14 场聊天、45 条消息、190 次物理请求；玩家消息成功，但 0 次 Memory 召回、0 个立场变化。另发现七个事件均在 `firedIds` 中、仅五个产生玩家可见投影，旧质量门误报事件不完整 | 世界事件门改用权威 `skippedIds` 而非公开事件流条数；玩家支持话术不再只问“担心什么”，改为明确询问提交与 Agenda 立场 | 事件门回归与完整台词证据链离线测试通过；真实路线仍缺召回与立场 | 保留事件门修复；单次 Day1 最终立场询问不符合剧情节奏，继续拆为两阶段 |
| pro_lin 两阶段玩家路线首次复跑 | seed 20260819 | 到达 Day7 18:00，13 场聊天、46 条消息、217 次物理请求；Day1 旧事询问成功触发 2 次 owner-safe 召回和 16 次真实向量命中。Day7 方舟连续超时，第二次邀请安全降级为拒绝，只有 1 条玩家消息，0 个立场变化 | 支持路线固定为 Day1 调查旧事、Day7 基于七日承诺与分歧询问最终立场；保留 NPC 的邀请拒绝权，不对技术失败或剧情结果写入强制接受 | Memory 目标已达到；立场路线因真实 provider timeout 未通过，临时 Run 已删除，待服务稳定复跑 | 保留两阶段节奏；不为验收降低立场门槛或绕过邀请 Agent |
| pro_lin 两阶段正式验收 | seed 20260819 | 全部质量门通过：Day7 18:00、7/7 事件、12 场聊天、44 条消息、2 条玩家消息；1 次真实 Memory 工具调用返回 8 个 owner-safe ID、8 次向量命中；林慧兰总体立场由 `unknown` 变为 `support`，11 次 Goal 变化、21 组关系变化、Repository 恢复成功，临时 Run 删除。其余 NPC 未形成足够立场且周慎之未授权，结局仍为 `no_submission`，玩家任务 `failed` | 不再调整 pro_lin 路线与章节效果协议 | 严格质量门通过；211 次物理请求、8 次 provider retry，少量新 Memory 向量待 backfill | 保留；证明玩家能改变可追溯立场但不会被脚本保证胜利 |
| pro_zhao 两阶段首次复跑 | seed 20260819 | 到达 Day7 18:00，11 场聊天、36 条消息、2 条玩家消息；1 次真实召回返回 7 个 ID、7 次向量命中，7 次 Goal 变化、17 组关系变化、Repository 恢复成功。最终一对一有玩家与 NPC 各 1 条消息，但 0 个立场变化；期间 13 次 provider retry 和 1 次 SpeechGeneration 失败 | Day7 问句不再同时要求回想承诺和表态，缩短为点名赵磊并要求直接选择支持、附条件或反对；增加可指定 NPC 的真实章节探针先验证 `npc_003` | 待定向探针与完整复跑 | 暂时保留短问句；仍不预设赵磊选择的值 |
| pro_zhao 直接表态正式验收 | seed 20260819 | 定向真实探针先通过：13 次请求、0 重试、赵磊发言并提交章节效果。完整路线全部质量门通过：Day7 18:00、7/7 事件、14 场聊天、51 条消息、2 条玩家消息；1 次召回返回 7 个 owner-safe ID、7 次向量命中；赵磊总体立场由 `unknown` 变为 `support`，13 次 Goal 变化、21 组关系变化、Repository 恢复成功，临时 Run 删除。其余 NPC 与周慎之未形成足够条件，结局仍为 `no_submission`，玩家任务 `failed` | 不再调整 pro_zhao 路线和直接表态问句 | 严格质量门通过；226 次物理请求、0 provider retry | 保留；与 pro_lin 一样证明玩家可改变单人立场但不能被脚本保证整体胜利 |

## 评估门槛

- 行为：全员 wait、主动性差异、目标重复、事件响应、17:00 边界。
- 聊天：重复、泄密、抢话、沉默、空闲结束和主动离场。
- Memory：召回频率、语义相关性、owner 隔离、重复/空泛节点。
- Goal/关系：推进依据、变化速度、familiarity、草稿即时生效与单次提交。
- 章节：三类结局可达性、Agenda 证据和玩家影响。

每次保留的修改必须写出调整前报告证据和调整后复跑数据。禁止为预期结局硬编码 NPC 选择。

## 多 NPC 玩家影响与结局可达性（最终严格验收已通过）

旧版 `pro_lin` 和 `pro_zhao` 每局只有少量、单目标玩家消息，最终都得到 `no_submission`；这批数据是单目标管线基线，不足以证明玩家能通过协商改变大任务结果。本阶段路线合同如下：

| 路线 | 公开信息驱动的玩家策略 | 预期用途（不是硬编码结果） |
|---|---|---|
| `observer` | 不发言，只旁观 | 世界推进基线 |
| `pro_lin` | 七天内接触多名 NPC，先询问公开条件，再汇总支持文社的可行承诺，最后向周慎之确认授权 | 成功分支可达性与多 NPC 立场联动 |
| `pro_zhao` | 低投入地支持赵磊，缺少跨角色条件协调 | 失败分支对照 |

每条路线至少运行 3 个不同 seed。每局和 route 汇总都要记录：玩家实际发言数、发生立场变化的 NPC 数、Goal 完成率、最终分支、输入/输出/总 Token、按配置单价计算的估算成本。价格未配置时报告 `n/a`，不得猜测金额。

玩家策略只可读取公开事件、公开聊天记录和玩家自己已看到的内容；不得读取或间接查询 NPC 私有立场、私有 Goal 状态、coreSecrets 或私有 Memory 来挑选话术。任何立场变化都必须由对应 NPC 自己生成的台词证据提交，后端只负责校验和落库；结局必须由最终数据库状态计算。

最终严格矩阵使用 9 个新格式真实样本，每条路线 3 个不同 seed 且 3/3 全部有效：`observer` 三局均为 0 玩家发言的 `no_submission`；`pro_lin` 三局均由至少 5 条玩家发言和至少 3 名 NPC 立场变化到达 `compromise_submitted`；`pro_zhao` 三局均有 2 条玩家发言和 3 名 NPC 立场变化，但因没有形成足够联盟与授权而得到 `no_submission + failed`。矩阵总折算成本为 `9.728042 CNY`，所有样本均完成 Repository 恢复和临时 Run 删除。一次并发压力诊断虽到达成功分支，但只写入 4 条玩家话术，被严格门禁排除，没有用于凑数。
