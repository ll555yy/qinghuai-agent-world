# 青槐巷可见体验设计

> 状态：样稿已确认，正式资产与前端实施完成
> 日期：2026-08-22
> 范围：正式人物头像、慎之旧书店单场景、场景聊天气泡、交互动效和时间氛围
> 依据：`FRONTEND_DESIGN.md`、`FRONTEND_EVENT_MAPPING.md`、`VISUAL_REFERENCE_RESEARCH.md`、现有前后端代码

## 1. 设计目标与边界

本轮把当前“可玩但仍像原型”的界面升级为可辨认、可观察、可长时间阅读的正式第一版。玩家在两秒内应看懂：

1. 场景是青槐老巷里的慎之旧书店；
2. 画面上每个人是谁；
3. 谁正在移动、思考、邀请、聊天或离开；
4. 当前哪几个人组成一场聊天；
5. 世界处于白天、黄昏还是闭店前；
6. 完整对话应该去右侧聊天面板阅读。

本轮只改变公开表现，不改变后端 Actor 坐标、事件、聊天、Agent、Memory、Goal、关系、立场、结局和时间推进规则。Phaser 动画不是状态机真相；服务器快照或新事件到达时，动画必须允许被打断并收敛到权威状态。

## 2. 现状审查

### 2.1 当前职责

| 层 | 当前职责 | 本轮决定 |
|---|---|---|
| FastAPI / WebSocket | Run、世界时间、Actor 状态与坐标、聊天和事件 | 保持权威，不修改协议 |
| Zustand | 保存公开快照、消息、邀请、加入请求、通知和单个场景提示 | 扩展为公开视觉 cue 队列或可消费视觉事件；不保存私有数据 |
| React | 顶栏、人物面板、完整聊天、请求卡、通知、日终和结局 | 保持；换用正式人物头像并统一纸张视觉语言 |
| Phaser | 几何书店、首字圆形人物、坐标插值、聊天圈、单条短气泡 | 接入正式场景、人物小头像、气泡管理、状态动画和时间覆盖层 |

### 2.2 当前主要缺口

- `BookstoreScene.drawBookstore()` 只绘制两组书架和一张长桌，没有入口、修书台、窗边、活动区、健康角和事件角；
- Actor 用姓名首字圆形代替头像，人物信息面板也只显示首字；
- `showBubble()` 每次直接创建一个 Phaser Text，不排队、不截断、不约束边缘，也会被连续消息覆盖；
- `SceneCue` 只有 `paper | refuse`，只稳定覆盖邀请和拒绝；
- `npc_thought_started`、`npc_waited`、加入/离开、普通 `message_created`、聊天静默等没有统一场景反馈；
- 移动由快照坐标变化触发，当前可视插值成立，但没有事件语义对应的朝向/状态反馈；
- 场景没有根据 `worldTime` 变化，17:00 和 17:50 主要只在 React 顶栏体现；
- 现有单元测试重在状态与隐私，没有资产映射、气泡、时间视觉和 reduced-motion 测试。

## 3. 美术方向

### 3.1 总体风格

采用“温暖旧纸绘本感的 2D 俯视书店 + 半写实简化胸像头像”。它不是像素 RPG，也不是全屏视觉小说：

- 场景以手绘质感和清楚轮廓为主，保持俯视空间可读性；
- 人物头像保留真实成年人的年龄、气质和职业差异，五官适度简化；
- React 面板沿用纸张、旧书签、木框和墨色，但减少当前偏现代 SaaS 的大圆角胶囊感；
- 动效低调、短促，强调状态变化而非角色表演；
- 所有可见内容只来自公开设定，不通过表情、道具或色彩泄露秘密。

### 3.2 色彩与材质

| 用途 | 色值建议 | 说明 |
|---|---|---|
| 旧纸基础 | `#F3EBD8` | 正文气泡、面板底色 |
| 泛黄高光 | `#FFF8E8` | 窗光与纸边 |
| 浅木 | `#C79A68` | 桌面、柜台和边框 |
| 深木 | `#684832` | 书架、描边和标题 |
| 墨色 | `#29251F` | 正文与主要轮廓 |
| 槐叶绿 | `#667A5A` | 邀请、聊天和安全状态 |
| 灯火橙 | `#C8793B` | 17:00 后提示与局部灯光 |
| 朱砂 | `#A64B3C` | 拒绝、错误和临界反馈 |
| 夜前灰蓝 | `#4D5060` | 17:50 后环境叠色，低透明度 |

材质只需轻纸纹、木纹、旧书脊和布料，不做高频噪点。头像透明背景，不嵌入文字、状态图标或专有 UI 框。

## 4. 慎之旧书店场景

### 4.1 固定画布与分层

- Phaser 逻辑画布保持 `880 × 534`，不改变现有 FIT 缩放；
- 正式场景底图建议按 `1760 × 1068` 生成并以 0.5 显示，兼顾 HiDPI 与体积；
- 场景资产不嵌入 UI 文本，店名由 React/Phaser 原生文本渲染；
- 分为 `background`、`actors`、`conversation`、`foreground`、`bubbles`、`time-overlay` 六个深度层；
- 前景遮挡物只能出现在画布边缘，并提供 0.55—0.75 透明度降级，不能遮死人物和右键命中区。

### 4.2 构图

```text
┌────────────────────────────────────────────────────────────┐
│ 西北受潮角  高书架/修书台        高书架       旧窗/安静座位 │
│ [Day2/5]    [周慎之区域]                      [沈星遥区域]  │
│                                                            │
│ 书架走道             中央长桌（三人聊天核心）              │
│                                                            │
│ 文社活动位      开放行动区                  健康角预留位    │
│                                                            │
│ 柜台/看店位                 主入口与青槐巷门光              │
└────────────────────────────────────────────────────────────┘
```

场景必须出现：

- 下方偏右的主入口和门外柔光；
- 左右不同高度的旧木书架及至少一条清楚走道；
- 左上周慎之修书台、台灯、裁纸垫和普通修书工具；
- 中央长桌，周围留出三人头像和气泡空间；
- 右上窗边单椅或窄桌，形成安静停留点；
- 左下可临时摆书法材料的文社活动位；
- 右下不固定具体医疗器械的空角，仅以可移动小桌表现健康角预留；
- 西北墙角轻微水痕或接水盆位置，为 Day2/Day5 提供视觉锚点，但不能在 Day1 就表现古籍秘密。

### 4.3 坐标映射

后端继续传递抽象 `{x, y}`。前端提供纯函数 `scenePosition()` 映射到五个主要停留区和安全插值区域；不把底图像素坐标回写后端。

第一版继续兼容当前线性映射，但把可活动边界限制为：

- X：`118—762`；
- Y：`248—436`；
- 头像上方至少预留 92px 气泡空间；
- 中央长桌不做物理碰撞，视觉上让人物落在桌边而不是桌面中心。

## 5. 人物视觉系统

### 5.1 统一输出规格

- 正式 NPC 胸像采用 `1536 × 1024` JPEG 三帧横向 sprite sheet，每帧 `512 × 1024`，顺序固定为 neutral / speaking / tense；玩家为单张中性头像；
- 图像生成服务实际输出统一深褐 vignette 背景，场景和面板使用圆形/圆角裁切；不再依赖生成结果提供透明 alpha；
- 同一人物三态共享头部比例、衣装、光源和裁切；
- 场景头像以圆角/圆形遮罩显示在 52—58px 范围；
- 人物面板使用 96—112px 方形裁切；
- 不在 PNG 内写姓名；姓名由 DOM/Phaser 文本绘制；
- 默认使用 `neutral`，当前作者最近产生公开 `message_created` 时短暂使用 `speaking`，公开紧张/拒绝反馈时短暂使用 `tense`；
- `tense` 只是当下表情，不等同于隐藏 tension 数值。

### 5.2 人物识别表

| Actor | 文件前缀 | 年龄/轮廓 | 发型与公开身份元素 | 姿态与主色 | 禁止暗示 |
|---|---|---|---|---|---|
| 林慧兰 `npc_001` | `lin-huilan` | 约六十余岁，清瘦、端正 | 银灰短卷发，素雅立领或针织外套，可有普通墨迹/笔夹 | 稳定直身，墨绿 + 米白 | 女儿疏离、林周旧怨 |
| 沈星遥 `npc_002` | `shen-xingyao` | 二十多岁，柔和偏窄脸 | 深色长发/低马尾，耳机作为公开日常物件 | 略收肩，雾紫 + 蓝灰 | 经济压力、私藏速写 |
| 赵磊 `npc_003` | `zhao-lei` | 三十多岁，轮廓利落 | 整洁短发，衬衫/休闲商务夹克 | 身体略前倾，砖红 + 棕金 | 私查经营情况、公司压力 |
| 陈月 `npc_004` | `chen-yue` | 三十岁左右，健康利落 | 清爽束发，浅色社区护士衣装元素，无机构标志 | 肩背舒展，青绿 + 白 | 配药失误、婚姻压力 |
| 周慎之 `npc_005` | `zhou-shenzhi` | 四十余岁，长窄脸、沉静 | 简短黑发带少量灰，深色衬衣/修书围裙 | 稳定低调，深灰蓝 + 木色 | 古籍来历、父辈心结 |
| 玩家 `player_001` | `player` | 年轻成年、抽象中性 | 简洁短中发剪影，不设明显职业配件 | 放松站姿，低饱和灰绿 | 性别、具体过往、真实动机 |

### 5.3 三态差异

- `neutral`：闭口、自然眉眼、面向略偏 3/4；
- `speaking`：嘴部轻张、目光更聚焦，不能做夸张笑脸；
- `tense`：眉眼收紧、嘴角克制或下压，保持人物原有冲突风格；林慧兰和周慎之尤其不能大怒吼叫。

### 5.4 场景状态装饰

- `thinking`：头像右上方三个小圆点依次变亮，600ms 循环；
- `approaching`：脚下软阴影拉长，状态文字“走近中”；
- `inviting`：槐叶绿细环呼吸一次后保持，不持续强闪；
- `chatting`：头像外圈使用人物主色，当前发言者外加一次 220ms 扩散环；
- `waiting/present`：无动画；
- `departed`：先出现中性离开标签，500—700ms 淡到 0，不显示推测原因。

## 6. 场景气泡系统

### 6.1 类型

内部视觉类型定义为：

```text
speech_npc
speech_player
invite
accept
refuse
join_request
join_accepted
leave_chat
thinking
closing_warning
system
```

| 类型 | 视觉 | 示例 |
|---|---|---|
| NPC 台词 | 旧纸白、深墨字、左下小尾巴 | “方案可以谈，但书店的底线不能破。” |
| 玩家台词 | 淡槐绿、深墨字 | 玩家公开发言 |
| 邀请/加入申请 | 旧纸底、槐绿描边 | “想和你聊聊” |
| 接受/加入成功 | 槐绿小标签 | “好，坐下说吧” / “加入了聊天” |
| 拒绝 | 低饱和朱砂底、暖白字 | “不了，我现在不想聊” |
| 思考 | 无文字三点 | `•••` |
| 临近闭店 | 灯火橙小标签 | “快到闭店时间了” |
| 系统 | 半透明旧纸标签，无角色口吻 | “离开了聊天” |

### 6.2 队列与生命周期

- 每个 Actor 拥有独立 FIFO 队列；
- 同一 Actor 同时只有一个活动气泡；
- 活动气泡结束时用 180—260ms 淡出，下一条再进入；
- 最多允许一个上一条气泡处于淡出阶段；
- 场景销毁、Actor 离场或 Run 更换时，取消 timer/tween 并销毁队列对象；
- 相同 `cueId` 只入队一次，WebSocket 重放不能重复显示；
- 队列只影响表现，不能阻止消息写入 ChatPanel。

### 6.3 文本规则

- 台词以 Unicode code point 计数，不截断半个代理对；
- 地图最多 2 行，目标宽度 176px；
- 建议最多 28 个中文字符；超出为前 27 个字符 + `…`；
- 邀请、接受、拒绝等系统短语上限 16 个字符；
- 完整文本始终保留在 `messages[conversationId]` 和 ChatPanel；
- 显示时长：`clamp(1800, 1200 + 字符数 × 55, 4200)` ms；
- reduced-motion 不缩短阅读时间，只移除非必要位移和缩放。

### 6.4 定位与避让

- 初始锚点为 Actor 世界坐标上方 72px；
- 气泡矩形必须限制在场景安全边界 `x=12—868, y=12—496`；
- 同一聊天圈中，如果两个活动/消退气泡重叠，较新的气泡向上偏移 42px；
- 仍重叠时再向角色外侧偏移 24px；
- 不允许气泡覆盖右侧 React 面板，因为 Canvas 自身在有面板时已经缩放；
- 三人聊天最多保留一个当前气泡和一个正在消退的上一条，第三条等待队列。

## 7. 动效规范

| 状态/事件 | 动效 | 普通时长 | reduced-motion |
|---|---|---:|---|
| 靠近 | `Sine.easeInOut` 到权威目标点，阴影轻变化 | 400—900ms | 100ms 淡变或立即定位 |
| 发出邀请 | 请求气泡 0.92→1，向上 6px | 180—240ms | 120ms 淡入 |
| 接受 | 双方色环各闪一次，聊天圈淡入 | 220—320ms | 120ms 淡入 |
| 拒绝 | 朱砂气泡；请求者水平回弹 4px 一次 | 260—360ms | 150ms 淡入，无回弹 |
| 加入聊天 | 新成员靠近，圈线扩大 6% 后回落 | 300—450ms | 圈线直接更新 |
| 离开聊天 | 聊天圈收敛到剩余成员，系统标签淡出 | 240—360ms | 120ms 淡变 |
| 当前发言 | `speaking` 头像 + 一次扩散环 | 220ms 入场，随气泡结束恢复 | 只切头像状态 |
| 思考 | 三点依次变亮 | 600ms 循环 | 静态三点 |
| 离开世界 | 中性标签后淡出人物 | 500—700ms | 150ms 淡出 |
| 世界事件 | 场景顶部纸条轻下落，React 事件记录持久化 | 260—320ms | 120ms 淡入 |
| 日终 | 场景颜色覆盖层加深，React 遮罩淡入 | 450—650ms | 150ms 淡入 |

新快照到达时：先终止旧坐标 tween，再从当前视觉位置走向新权威位置；若 Actor 已 `departed`，离场优先；若场景重建，直接从快照最终状态绘制，不补播历史动画。

## 8. 时间氛围

视觉阶段通过纯函数由 `WorldTime` 派生，不创建第二个计时器：

| 时间 | 覆盖层/局部光 | React 信息 |
|---|---|---|
| 09:00—15:59 | 日光米白，覆盖层 alpha 0—0.04 | 正常顶栏 |
| 16:00—16:59 | 金色 `#D79A55`，alpha 0.03→0.10 | 无额外打扰 |
| 17:00—17:49 | 暖橙 `#C8793B`，alpha 0.08→0.15，台灯光晕更明显 | 顶栏提示停止新邀请 |
| 17:50—17:59 | 灰蓝 `#4D5060` 与暖灯组合，alpha 0.14→0.20 | 低打扰倒计时 |
| 18:00 | 场景冻结命中；覆盖层至 0.28，随后 React 日终遮罩 | 强制结束聊天与日终说明 |

插值以 `hour * 60 + minute` 计算。重连时直接得到当前值，不从 09:00 补播。跨日后由新快照回到新一天阶段。

## 9. 公开事件到视觉表现的映射

| 后端事件 | 可用公开 payload | Phaser | React | 注意 |
|---|---|---|---|---|
| `npc_thought_started` | actorId、worldTime | 开始 thinking 三点 | 不弹通知 | 后续 skipped/waited/移动/邀请/聊天状态停止 |
| `npc_thought_skipped` | actorId、reason、worldTime | 清除 thinking | 不展示内部 reason | 聊天中的 NPC 保持 chatting |
| `npc_waited` | actorId、worldTime/reason | 清除 thinking，恢复 waiting | 无 | invalid reason 不必对玩家解释 |
| `actor_movement_started` | actorId、targetActorId | 状态 approaching、准备移动反馈 | 无 | 真正终点等 completed/snapshot |
| `actor_movement_completed` | actorId、targetActorId、position | 终止旧 tween，收敛到 position | 无 | 服务器位置权威 |
| `invitation_requested` | invitationId、双方 actorId | 发起者显示 invite 气泡 | 目标为玩家时显示请求卡 | 不显示私有 intent/goalId |
| `invitation_accepted` | invitationId、conversationId | accept 反馈，等 conversation 建圈 | 成功通知 | 参与者以 conversation 为准 |
| `invitation_refused` | invitationId、targetActorId | 目标显示 refuse 气泡 | warning 通知 | 请求者回 waiting 由快照确认 |
| `invitation_expired` | invitationId、双方 actorId、expiredAt | closing_warning/system 标签 | 截止通知 | 17:00 后不补邀请动画 |
| `join_request_created` | public joinRequest | 申请者显示 join_request | 玩家需审批时显示请求卡 | 不显示 NPC 审批推理 |
| `join_request_resolved` | status、applicantActorId 等 | accepted/refused/expired 反馈 | 对玩家显示结果 | conversation joined 仍以事件为准 |
| `conversation_created` | conversation | 参与者切 chatting，聊天圈淡入 | 可打开聊天面板 | 不伪造开场台词 |
| `conversation_participant_joined` | conversation、actorJoined | 入圈动画，join_accepted 标签 | 插入系统消息 | 第三人不继承前段记忆是后端规则 |
| `conversation_participant_left` | conversation、actorLeft | 离圈，聊天圈收敛 | 插入系统消息 | 不推测离开原因 |
| `message_created` | conversationId、authorActorId、text（玩家可见时） | 作者 speaking + speech 气泡 | 完整文本追加 | 无 text 时绝不造台词 |
| `conversation_activity` | conversationId、reason | 可显示中性忙碌/恢复状态 | 非文本提示 | 不生成台词气泡 |
| `conversation_idle` | conversationId、idleCount | 聊天圈轻呼吸一次 | “短暂沉默” | 两轮判定仍在后端 |
| `conversation_closed` | conversation | 清圈，参与者恢复或离场 | 关闭输入，显示 closeReason | 日终关闭由时间表现统一收束 |
| `world_event_occurred` | event、worldTime | 场景顶部短纸条/角标 | 横幅与事件记录 | 只显示玩家可见事件 |
| `world_day_started` | worldTime | 直接应用新日光阶段 | 关闭日终遮罩 | 不补播离线时间 |
| `world_day_ended` | worldTime、reason | 进入 18:00 覆盖层 | 日终遮罩 | 公开聊天已由后端关闭 |
| Actor 状态 `departed` | snapshot.actorStates | 离场动画或重连时直接隐藏 | 公开事件区说明 | 不展示推测原因 |

## 10. React 组件调整

### ActorPanel

- 用正式 `neutral` 胸像替换首字圆形；
- 头像加载失败时回退到当前首字占位；
- 保留公开背景、公开印象和隐私说明；
- 不根据当前关系或秘密换表情。

### ChatPanel

- 保留完整历史、自由输入和系统消息；
- 消息作者旁增加 28—32px `neutral` 头像，提高三人聊天归属感；
- 当前新到 NPC 消息可短暂使用 `speaking`，但历史恢复全部用 neutral；
- 不因场景气泡截断而截断聊天面板内容。

### 请求卡、通知与顶栏

- 请求卡使用邀请者头像、姓名和明确接受/拒绝按钮；
- 17:50 倒计时保持低打扰，不盖住请求卡；
- 通知继续作为短动画失败或错过时的持久兜底；
- 所有中文仍由 DOM/Phaser 文本绘制，不放进 AI 图片。

## 11. 资产结构与映射

样稿阶段：

```text
core/frontend/public/assets/samples/
  lin-huilan-states-sample.png
  zhou-shenzhi-states-sample.png
  bookstore-composition-sample.png
  speech-bubbles-sample.png
  combined-ui-sample.png
```

确认后的正式结构：

```text
core/frontend/public/assets/
  actors/
    lin-huilan-states.jpg
    shen-xingyao-states.jpg
    zhao-lei-states.jpg
    chen-yue-states.jpg
    zhou-shenzhi-states.jpg
    player-neutral.png
  scenes/
    shenzhi-bookstore-background.jpg
    shenzhi-bookstore-foreground.png   # 仅在确有必要时拆分
  ui/
    ...                               # 只放必须为位图的纹理
```

代码中建立明确 `actorId -> sprite sheet + frame` 映射。未知 Actor、未知状态或图片加载失败时回退到 neutral，再失败才回退首字圆形。不得根据数组下标决定人物身份或头像。

## 12. 样稿验收标准

阶段三只生成并展示以下五张样稿，不进入正式代码：

1. 林慧兰三态头像合图；
2. 周慎之三态头像合图；
3. 慎之旧书店俯视构图；
4. 普通台词与拒绝气泡样式板；
5. 1440×900 的 React + Phaser 组合效果图。

样稿通过条件：

- 两人一眼能区分年龄、气质和公开身份；
- 三态是同一人物，不发生换脸、换衣或裁切漂移；
- 头像没有文字、logo、水印或秘密暗示；
- 书店包含所有要求区域，中央聊天区有足够留白；
- 气泡可承载中文，但样稿文字只是布局示意，正式文字由代码渲染；
- 组合图能看出地图、气泡、顶栏和侧栏的信息层级；
- 不与 Coffee Talk、Tiny Bookshop、AI Town 或 Smallville 的可识别人物/地图/UI 近似。

样稿确认前不得批量制作剩余人物，不得把样稿文件接入生产代码。

## 13. 实施与测试计划（确认后）

### 13.1 可测试纯逻辑

拆出不依赖 Phaser 实例的模块，至少包含：

- `actorAssets.ts`：Actor 与状态资产映射、fallback；
- `bubblePolicy.ts`：Unicode 截断、时长、类型主题、安全边界和避让；
- `timeVisuals.ts`：09:00/16:00/17:00/17:50/18:00 阶段与插值；
- 必要时 `sceneCueMapper.ts`：公开 RunEvent 到视觉 cue，不读取私有字段。

### 13.2 单元测试

覆盖：Actor 映射、未知 fallback、三态选择、FIFO、截断、时长上下限、避让、reduced-motion、departed、时间边界、新快照中断动画的权威收敛。

### 13.3 Playwright 与视觉检查

覆盖正式地图和六人、右键人物卡、邀请接受/拒绝、加入、三人聊天、完整消息、17:50、18:00 和 reduced-motion。以 1440×900、1280×720、窄桌面截图检查人物辨认、气泡遮挡、中文裁切、菜单命中和时间氛围。

### 13.4 质量门禁

运行 `pnpm lint`、`pnpm typecheck`、`pnpm test`、`pnpm build`、Playwright E2E；记录构建体积前后变化。图片走 `public` 静态资源，不转 base64，不引入新的大型 UI 框架。测试结束不遗留前后端开发服务。

## 14. 资产合法性

样稿和正式图片优先使用内置图像生成工具产生原创内容，生成记录写入 `VISUAL_ASSET_MANIFEST.md`。本轮不使用外部参考图作为图像输入；网络项目只提供文字层面的设计原则。任何后续 CC0 或可商用第三方资产进入仓库前，仍需记录作者、原始链接、许可证、SHA-256、修改内容和项目路径。
