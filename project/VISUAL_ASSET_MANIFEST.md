# 视觉资产来源清单

> 建立日期：2026-08-22
> 当前状态：没有把任何第三方代码或美术素材导入项目；7 张原创 AI 正式资产已接入生产代码。
> 管理原则：只有原创、AI 生成、CC0，或明确允许项目所需使用方式的资产，才可以进入正式资源目录。

## 1. 当前仓库视觉资产状态

截至 2026-08-22 正式实施完成时：

- `public/assets/actors` 已包含五名 NPC 三态 sprite sheet 和玩家中性头像，`public/assets/scenes` 已包含正式书店背景；
- 聊天气泡、聊天圈、时间覆盖层和加载失败占位由项目内 TypeScript/Phaser 几何图形与 CSS 原创绘制；
- 本轮没有下载、复制、改编或提交任何外部图片、字体、音频、地图、图块、图标或示例代码；正式资产由内置图像生成工具从文字描述生成。

因此当前没有需要随发行物附带的新增第三方视觉资产署名。正式资产的生成记录见第 4 节。

## 2. 已核验但尚未导入的候选资产

| 候选 | 作者/发布方 | 原始链接 | 许可证 | 当前决定 | 项目内路径 |
|---|---|---|---|---|---|
| RPG Urban Pack | Kenney | https://kenney.nl/assets/rpg-urban-pack | CC0 | 仅作地图模块化参考；若原创场景制作受阻，可单独评估少量基础 tile，导入前再次核验 | 未导入 |
| Roguelike/RPG Pack | Kenney | https://kenney.nl/assets/roguelike-rpg-pack | CC0 | 仅作家具剪影和尺寸参考；与预定画风差异较大，当前不使用 | 未导入 |

“已核验”只说明官方页面当前标注的许可证满足基本条件，不代表必须采用。正式视觉优先使用本项目原创或 AI 生成资产。

## 3. 仅可参考、禁止直接导入的内容

| 来源 | 原因 |
|---|---|
| Phaser 官方 Examples 中的图片、音频和其他示例资产 | 仓库明确说明代码是 MIT，但资产不是，且部分不可用于商业或含广告的游戏 |
| Generative Agents / Smallville 的背景、家具和人物图 | 仓库代码为 Apache-2.0，但 README 为美术分别列出作者；未逐项取得资产许可 |
| AI Town 仓库内来自不同作者/站点的 tilesheet、UI 和人物资源 | 顶层代码 MIT 不自动覆盖第三方美术；必须逐项追踪原始来源和许可 |
| Coffee Talk 的人物、咖啡店场景、UI、字体风格和截图 | 商业专有版权，只可研究阅读节奏与室内氛围，禁止复制或近似临摹 |
| Tiny Bookshop 的人物、书店场景、装饰物、UI 和截图 | 商业专有版权/EULA，不属于可复用素材 |
| Rundale 的代码、名称、logo 与第三方地图内容 | 代码 GPL-3.0-only，名称/logo 不在 GPL 授权内，地图另有 ODbL/CC-BY 等要求；本项目不复制 |
| Dialogic 的内置 UI、图标或示例内容 | 本项目技术栈不同且没有必要复制；只参考表现模块化原则 |

## 4. 正式资产登记表

任何文件进入 `core/frontend/public/assets` 时，必须先在下表新增记录。不能用“网络素材”“AI 生成”“免费素材”等模糊描述。

| Asset ID | 项目内文件 | 类型/用途 | 作者或生成方式 | 原始链接/生成记录 | 许可证 | 下载/生成日期 | SHA-256 原件 | 修改内容 | 署名位置 | 审核状态 |
|---|---|---|---|---|---|---|---|---|---|---|
| `actor_lin_huilan_states_v1` | `core/frontend/public/assets/actors/lin-huilan-states.jpg` | 林慧兰 neutral/speaking/tense 三帧 | Codex 内置图像生成工具 | 退休语文教师、银灰卷发、墨绿衣装、三种克制状态、统一深褐背景 | 项目原创 AI 生成 | 2026-08-22 | `7e5378eb1d40930711369b45fb81c345e56814fc6a4e78aa0722a8e63d6a9bc5` | PNG 原件以质量 88 编码为 JPEG；运行时按帧与圆形 mask 显示 | 不需要 | `approved` |
| `actor_shen_xingyao_states_v1` | `core/frontend/public/assets/actors/shen-xingyao-states.jpg` | 沈星遥三帧 | Codex 内置图像生成工具 | 二十七岁插画师、深发蓝灰衣装、安静/轻声/退缩 | 项目原创 AI 生成 | 2026-08-22 | `1a5c996dc15c090a3f5cbd16d93a75ff22bda60aba334b20a08f436f33eca16d` | PNG 原件以质量 88 编码为 JPEG；运行时按帧与圆形 mask 显示 | 不需要 | `approved` |
| `actor_zhao_lei_states_v1` | `core/frontend/public/assets/actors/zhao-lei-states.jpg` | 赵磊三帧 | Codex 内置图像生成工具 | 三十六岁销售主管、砖红夹克、外向/发言/紧绷 | 项目原创 AI 生成 | 2026-08-22 | `b943a06858e30a0951842ac854ccabce1b93a999a34eb630b92cb0e052cd36fe` | PNG 原件以质量 88 编码为 JPEG；运行时按帧与圆形 mask 显示 | 不需要 | `approved` |
| `actor_chen_yue_states_v1` | `core/frontend/public/assets/actors/chen-yue-states.jpg` | 陈月三帧 | Codex 内置图像生成工具 | 三十二岁社区护士、短发砖红外套、警醒/提醒/急躁 | 项目原创 AI 生成 | 2026-08-22 | `e8ddbd8d65c44fa0999e6661b2abd61f4860829fa863395b464e6121620a4e83` | PNG 原件以质量 88 编码为 JPEG；运行时按帧与圆形 mask 显示 | 不需要 | `approved` |
| `actor_zhou_shenzhi_states_v1` | `core/frontend/public/assets/actors/zhou-shenzhi-states.jpg` | 周慎之三帧 | Codex 内置图像生成工具 | 四十三岁旧书店老板、深色衣装和修书围裙、沉静/简短/划界 | 项目原创 AI 生成 | 2026-08-22 | `5206a3c188c1dc8619e545f425cc01521836b661a96c3ff778d3f721e073707a` | PNG 原件以质量 88 编码为 JPEG；运行时按帧与圆形 mask 显示 | 不需要 | `approved` |
| `actor_player_neutral_v1` | `core/frontend/public/assets/actors/player-neutral.png` | 玩家低信息量中性头像 | Codex 内置图像生成工具 | 年轻中国成年人、中性呈现、无职业/性格/经历暗示 | 项目原创 AI 生成 | 2026-08-22 | `6ea8b30bd5a60f04be6f752ff66133f181a8182cf536702a06d21c59f77a7d86` | 保留 PNG 以避免 Phaser 在部分 Chromium 会话中拒绝处理该 JPEG；运行时圆形 mask 显示 | 不需要 | `approved` |
| `scene_shenzhi_bookstore_v1` | `core/frontend/public/assets/scenes/shenzhi-bookstore-background.jpg` | 正式书店俯视背景 | Codex 内置图像生成工具 | 青槐巷旧书店、入口、书架、修书台、中央长桌、窗边和西北受潮角 | 项目原创 AI 生成 | 2026-08-22 | `61e9a1e4296c38e402ce3ae376121f96d4d2633119208e36fd30ed141c142c05` | PNG 原件以质量 88 编码为 JPEG；Phaser cover 缩放到 880×534 | 不需要 | `approved` |

审核状态只允许：

- `candidate`：候选，不能进入正式构建；
- `approved`：来源与许可证已核验，可以使用；
- `rejected`：许可、质量或风格不合适；
- `replaced`：已由新资产替换，保留历史记录。

## 5. AI 生成资产的补充记录

AI 生成的头像或场景也必须登记，并额外保存：

- 使用的生成工具/模型；
- 生成日期和提示词摘要；
- 是否使用参考图，以及参考图是否拥有可用于生成的权利；
- 人工修改的软件与修改范围；
- 是否检查明显商标、真实机构标志、公众人物相似性和与现有商业角色高度近似的问题。

如果生成服务的条款无法确认允许项目所需的发布方式，该资产保持 `candidate`，不能进入正式构建。
