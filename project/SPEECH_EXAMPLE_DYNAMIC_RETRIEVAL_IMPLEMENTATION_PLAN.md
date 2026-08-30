# NPC Speech Example Dynamic Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an intent-driven, per-NPC vector retriever that injects up to three manually curated dialogue examples only after a final `ChatDecision.action=speak`.

**Architecture:** Load immutable speech examples from a ninth scenario YAML into `ScenarioRegistry`, index `situation + intendedMove` with the existing 2048-dimension embedding boundary, and keep example vectors in a small process-local cache. `RunService` calls the independent retriever after the final chat decision and before SpeechGeneration; current visible messages stay in the generation prompt, while only final `intent` is embedded for example selection.

**Tech Stack:** Python 3.12, dataclasses, PyYAML, asyncio, existing Ark embedding adapter, FastAPI lifespan dependency injection, pytest.

## Global Constraints

- Keep the existing `ChatDecision -> need_memory -> retrieve_owned_memories -> ChatDecision` graph unchanged.
- Run example retrieval only for a final, validated `action=speak`; never for `wait`, `leave_chat`, or the intermediate `need_memory` result.
- Use only final `intent` as the semantic query in v1. Do not concatenate current-round messages, private Memory content, persona text, or generated replies into the query vector.
- Hard-filter candidates by `npcId`; never return another NPC's example.
- Return at most three examples, ordered deterministically by descending cosine similarity and then ascending `exampleId`.
- Index only `situation + intendedMove`; never embed `reply`.
- Inject only `situation`, `intendedMove`, and `reply` into SpeechGeneration. Do not expose IDs, vectors, scores, or retrieval errors to the model.
- Retrieval adds no LLM call, does not acquire the model semaphore, and does not hold `Run.lock` while awaiting embeddings.
- Retrieval failure is fail-open: continue SpeechGeneration without examples and never convert `speak` to `wait`.
- Keep the existing SpeechGeneration temperature unchanged in this feature.
- Use only manually authored, reviewed scenario examples in v1; do not learn from runtime dialogue or feedback.
- Preserve existing stale-round, visibility, memory ownership, draft, chapter-effect, publication-order, and day-boundary behavior.

---

## File Map

**Create:**

- `core/scenario/NPC_SPEECH_EXAMPLES.yaml` — canonical reviewed v1 dialogue-example corpus.
- `core/backend/app/persistence/speech_example_retriever.py` — retriever protocol, hit/result types, vector cache, cosine ranking, fail-open error codes.
- `test/backend/unit/test_speech_example_retriever.py` — isolated vector ranking, scope, determinism, cache, and failure tests.

**Modify:**

- `core/backend/app/scenario/models.py` — immutable `SpeechExampleDefinition` and `ScenarioRegistry.speech_examples`.
- `core/backend/app/scenario/registry.py` — public scenario-model export.
- `core/backend/app/scenario/loader.py` — ninth YAML declaration, strict parsing, cross-reference and minimum-corpus validation.
- `core/backend/app/main.py` — create one embedding client independently of persistence backend; inject the speech-example retriever.
- `core/backend/app/orchestration/run_service.py` — retrieve after final speak decisions, outside `Run.lock`; inject prompt payload for normal, opener, and join speech.
- `core/backend/app/ai/decision_service.py` — require specific intents and define how SpeechGeneration uses examples.
- `test/backend/unit/test_scenario_loader.py` — corpus validation and immutability tests.
- `test/backend/unit/test_message_driven_chat_rounds.py` — normal-round timing, intent query, per-NPC isolation, prompt shape, and failure tests.
- `test/backend/unit/test_join_requests.py` — opener/join example-injection coverage.
- `test/backend/unit/test_decision_service_json.py` — protocol-rule regression assertions.
- `test/backend/integration/test_playable_loop_smoke.py` — no-regression playable path with a fake example retriever.
- `README.md` — scenario-file inventory and behavior note.
- `.env.example` — clarify that `ARK_EMBEDDING_MODEL` enables both private-memory vector recall and speech-example retrieval.

**Do not create or modify:**

- PostgreSQL tables or Alembic migrations. The corpus is immutable and small; v1 vectors live in a process-local cache.
- Memory models, `MemoryRetriever`, `RetrieveOwnedMemoriesTool`, LangGraph routes, or run persistence codec.
- Frontend code or public APIs.

---

### Task 1: Add the immutable scenario corpus

**Files:**

- Create: `core/scenario/NPC_SPEECH_EXAMPLES.yaml`
- Modify: `core/backend/app/scenario/models.py`
- Modify: `core/backend/app/scenario/registry.py`
- Modify: `core/backend/app/scenario/loader.py`
- Test: `test/backend/unit/test_scenario_loader.py`

**Interfaces:**

- Produces: `SpeechExampleDefinition(example_id, npc_id, situation, intended_move, reply)`.
- Produces: `ScenarioRegistry.speech_examples: Mapping[str, SpeechExampleDefinition]` keyed by `exampleId`.
- Produces: immutable, validated examples consumed by `VectorSpeechExampleRetriever` in Task 2.

- [ ] **Step 1: Write failing loader and immutability tests**

Add these assertions and helpers to `test/backend/unit/test_scenario_loader.py`:

```python
from dataclasses import FrozenInstanceError, replace
from shutil import copyfile

SCENARIO_FILENAMES = (
    "NPC_PERSONAS.yaml",
    "PLAYER_PROFILE.yaml",
    "INITIAL_TOPICS.yaml",
    "INITIAL_GOALS.yaml",
    "INITIAL_RELATIONSHIPS.yaml",
    "INITIAL_MEMORIES.yaml",
    "WORLD_EVENTS_DAY1_7.yaml",
    "CHAPTER_AGENDAS.yaml",
    "NPC_SPEECH_EXAMPLES.yaml",
)


def _copy_scenario(source: Path, target: Path) -> None:
    for filename in SCENARIO_FILENAMES:
        copyfile(source / filename, target / filename)


def test_speech_example_corpus_is_loaded_and_scoped(registry) -> None:
    counts = {
        npc.actor_id: sum(
            example.npc_id == npc.actor_id
            for example in registry.speech_examples.values()
        )
        for npc in registry.npcs
    }
    assert counts == {npc.actor_id: 8 for npc in registry.npcs}
    example = registry.speech_examples["npc001_refuse_mediate_01"]
    assert example.npc_id == "npc_001"
    assert "代为" in example.situation
    assert "拒绝" in example.intended_move
    assert example.reply == "这话我替你递不合适。你若真有诚意，自己同他说。"


def test_speech_examples_are_immutable(registry) -> None:
    with pytest.raises(TypeError):
        registry.speech_examples["new"] = registry.speech_examples[
            "npc001_refuse_mediate_01"
        ]  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        registry.speech_examples["npc001_refuse_mediate_01"].reply = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        ("npc001_refuse_mediate_01", "npc001_greet_01", "duplicate example ID"),
        ("npcId: npc_001", "npcId: npc_missing", "unknown npcId"),
        (
            'situation: "别人请求她代为向第三方说情"',
            'situation: ""',
            "must be a non-empty string",
        ),
    ],
)
def test_invalid_speech_example_has_file_and_field(
    tmp_path: Path,
    old: str,
    new: str,
    match: str,
) -> None:
    source = Path(__file__).resolve().parents[3] / "core" / "scenario"
    _copy_scenario(source, tmp_path)
    path = tmp_path / "NPC_SPEECH_EXAMPLES.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace(old, new, 1), encoding="utf-8")
    with pytest.raises(ScenarioValidationError, match=match):
        ScenarioLoader(tmp_path).load()


def test_each_npc_requires_at_least_eight_speech_examples(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[3] / "core" / "scenario"
    _copy_scenario(source, tmp_path)
    path = tmp_path / "NPC_SPEECH_EXAMPLES.yaml"
    text = path.read_text(encoding="utf-8")
    start = text.index("  - exampleId: npc001_close_01")
    end = text.index("  - exampleId: npc002_greet_01")
    path.write_text(text[:start] + text[end:], encoding="utf-8")
    with pytest.raises(ScenarioValidationError, match="npc_001 requires at least 8 examples"):
        ScenarioLoader(tmp_path).load()
```

Also change the existing `test_eight_real_yaml_files_load` name to `test_real_yaml_files_load` and assert `len(registry.speech_examples) == 40`.

- [ ] **Step 2: Run the loader tests and verify failure**

Run:

```powershell
python -m pytest test/backend/unit/test_scenario_loader.py -q
```

Expected: FAIL because `ScenarioRegistry` has no `speech_examples`, the ninth YAML is missing, and the loader does not parse it.

- [ ] **Step 3: Add the immutable model and loader**

Add to `core/backend/app/scenario/models.py`:

```python
@dataclass(frozen=True, slots=True)
class SpeechExampleDefinition:
    example_id: str
    npc_id: str
    situation: str
    intended_move: str
    reply: str
```

Add this field to `ScenarioRegistry` immediately after `npc_personas`:

```python
speech_examples: Mapping[str, SpeechExampleDefinition]
```

Copy it in `ScenarioRegistry.copy()`:

```python
speech_examples=deepcopy(dict(self.speech_examples)),
```

Export `SpeechExampleDefinition` from `core/backend/app/scenario/registry.py` alongside the other immutable definitions.

In `core/backend/app/scenario/loader.py`:

```python
from .models import SpeechExampleDefinition

SCENARIO_FILES = (
    "NPC_PERSONAS.yaml",
    "PLAYER_PROFILE.yaml",
    "INITIAL_TOPICS.yaml",
    "INITIAL_GOALS.yaml",
    "INITIAL_RELATIONSHIPS.yaml",
    "INITIAL_MEMORIES.yaml",
    "WORLD_EVENTS_DAY1_7.yaml",
    "CHAPTER_AGENDAS.yaml",
    "NPC_SPEECH_EXAMPLES.yaml",
)
```

Call the new parser after actors load and pass its result into the registry:

```python
speech_examples = self._load_speech_examples(
    files["NPC_SPEECH_EXAMPLES.yaml"], actors
)

return ScenarioRegistry(
    actors=actors,
    npcs=npcs,
    npc_personas=npc_personas,
    speech_examples=speech_examples,
    # existing fields unchanged
)
```

Implement strict parsing:

```python
@staticmethod
def _load_speech_examples(
    data: Mapping[str, Any],
    actors: Mapping[str, ActorDefinition],
) -> Mapping[str, SpeechExampleDefinition]:
    filename = "NPC_SPEECH_EXAMPLES.yaml"
    rows = _list(data.get("examples"), file=filename, field="examples")
    parsed: dict[str, SpeechExampleDefinition] = {}
    counts: dict[str, int] = {
        actor_id: 0 for actor_id, actor in actors.items() if actor.kind == "npc"
    }
    for index, raw in enumerate(rows):
        field = f"examples[{index}]"
        row = _mapping(raw, file=filename, field=field)
        example_id = _str(row.get("exampleId"), file=filename, field=f"{field}.exampleId")
        if example_id in parsed:
            raise ScenarioValidationError(
                "duplicate example ID", file=filename, field=f"{field}.exampleId"
            )
        npc_id = _str(row.get("npcId"), file=filename, field=f"{field}.npcId")
        actor = actors.get(npc_id)
        if actor is None or actor.kind != "npc":
            raise ScenarioValidationError(
                "unknown npcId", file=filename, field=f"{field}.npcId"
            )
        parsed[example_id] = SpeechExampleDefinition(
            example_id=example_id,
            npc_id=npc_id,
            situation=_str(row.get("situation"), file=filename, field=f"{field}.situation"),
            intended_move=_str(
                row.get("intendedMove"), file=filename, field=f"{field}.intendedMove"
            ),
            reply=_str(row.get("reply"), file=filename, field=f"{field}.reply"),
        )
        counts[npc_id] += 1
    for npc_id, count in sorted(counts.items()):
        if count < 8:
            raise ScenarioValidationError(
                f"{npc_id} requires at least 8 examples",
                file=filename,
                field="examples",
            )
    return MappingProxyType(parsed)
```

- [ ] **Step 4: Add the reviewed 40-example YAML corpus**

Create `core/scenario/NPC_SPEECH_EXAMPLES.yaml` with `version: 1`, `status: confirmed`, and exactly these eight examples per NPC:

```yaml
version: 1
status: confirmed

examples:
  - exampleId: npc001_greet_01
    npcId: npc_001
    situation: "熟人刚进屋，她自然招呼对方坐下"
    intendedMove: "简短招呼并照顾对方，不主动展开长篇寒暄"
    reply: "来了？先坐。外头风大，茶还热着。"
  - exampleId: npc001_answer_conditions_01
    npcId: npc_001
    situation: "别人直接询问她是否支持留下旧书"
    intendedMove: "明确表示支持，同时提出规矩和公开条件"
    reply: "我赞成把书留下来，但账目得说明白。好心办事，也得有个章法。"
  - exampleId: npc001_refuse_mediate_01
    npcId: npc_001
    situation: "别人请求她代为向第三方说情"
    intendedMove: "委婉拒绝代为沟通，把行动责任交还给请求者"
    reply: "这话我替你递不合适。你若真有诚意，自己同他说。"
  - exampleId: npc001_disagree_public_01
    npcId: npc_001
    situation: "有人在众人面前替所有人擅自作主"
    intendedMove: "克制但锋利地指出对方越界，不当场发作"
    reply: "小赵，这话说得太满了。事情还没弄清，先别替大家作主。"
  - exampleId: npc001_probe_commitment_01
    npcId: npc_001
    situation: "对方笼统表示愿意帮忙"
    intendedMove: "追问可兑现的具体承诺，避免空泛表态"
    reply: "你说愿意帮忙，我记下了。具体能做到哪一步？"
  - exampleId: npc001_avoid_old_matter_01
    npcId: npc_001
    situation: "别人突然提起她不愿公开谈论的旧事"
    intendedMove: "用停顿压住情绪并暂时划清边界"
    reply: "那件事……先不提了。该说的时候，我自然会说。"
  - exampleId: npc001_deescalate_01
    npcId: npc_001
    situation: "争执中有人提高声音"
    intendedMove: "先恢复体面和秩序，再继续讨论"
    reply: "先把声音放低。话说重了，后头就不好收了。"
  - exampleId: npc001_close_01
    npcId: npc_001
    situation: "讨论已经僵住且继续说只会重复"
    intendedMove: "自然结束当下讨论，给所有人留出冷静空间"
    reply: "今天就到这儿吧。余下的，等大家都静一静再谈。"

  - exampleId: npc002_greet_01
    npcId: npc_002
    situation: "熟人来到她正在停留的地方"
    intendedMove: "轻声确认对方到来并留出位置，不热情招揽"
    reply: "……你也来了。那边还有位置。"
  - exampleId: npc002_answer_boundary_01
    npcId: npc_002
    situation: "别人直接询问她是否愿意提供人物插画"
    intendedMove: "简短答应，但明确人物故事需要本人同意"
    reply: "可以画。但人物故事要先问过本人。"
  - exampleId: npc002_refuse_private_work_01
    npcId: npc_002
    situation: "别人追问她不愿公开的稿件安排"
    intendedMove: "礼貌结束私人工作话题，不提供额外解释"
    reply: "这个我不想聊。稿子的事，我自己会安排。"
  - exampleId: npc002_disagree_noise_01
    npcId: npc_002
    situation: "有人把热闹直接等同于好效果"
    intendedMove: "低调表达不同意见，不抢夺讨论主导权"
    reply: "我不太认同。热闹不一定就是好事。"
  - exampleId: npc002_probe_scope_01
    npcId: npc_002
    situation: "对方用模糊的大家指代参与者"
    intendedMove: "要求澄清实际涉及的人，避免替他人决定"
    reply: "你说的‘大家’，具体包括谁？"
  - exampleId: npc002_hesitate_01
    npcId: npc_002
    situation: "她被要求立即做出不确定的决定"
    intendedMove: "暂停回应，为自己争取思考空间"
    reply: "……等等。让我先想一下。"
  - exampleId: npc002_soften_01
    npcId: npc_002
    situation: "对方误以为她在责怪自己"
    intendedMove: "澄清没有责怪，同时请求放慢推进速度"
    reply: "我不是在怪你。只是这件事，能不能慢一点？"
  - exampleId: npc002_close_01
    npcId: npc_002
    situation: "她精力不足并准备离开讨论"
    intendedMove: "用未完成的工作作简短说明后离开"
    reply: "我先回去了。画还没收尾。"

  - exampleId: npc003_greet_01
    npcId: npc_003
    situation: "他碰见一群熟人正在讨论事情"
    intendedMove: "熟络加入并主动表示自己有想法"
    reply: "哟，都在呢？正好，我还真有个想法。"
  - exampleId: npc003_answer_feasibility_01
    npcId: npc_003
    situation: "别人询问一个合作方案能不能落地"
    intendedMove: "先确认可行，再把话题拉到规模、预算和人手"
    reply: "能做，关键看你想做到什么规模。预算和人手先摆出来。"
  - exampleId: npc003_refuse_bad_deal_01
    npcId: npc_003
    situation: "熟人希望他接下明显不划算的合作"
    intendedMove: "直接拒绝，同时区分人情与商业风险"
    reply: "这事我不接。人情归人情，坑不能明知道还往里跳。"
  - exampleId: npc003_disagree_speed_01
    npcId: npc_003
    situation: "讨论方案推进得非常保守缓慢"
    intendedMove: "明确反对拖延并强调机会窗口"
    reply: "讲真的，这么做太慢了。机会不等人。"
  - exampleId: npc003_probe_commitment_01
    npcId: npc_003
    situation: "对方反复讨论却没有明确是否要做"
    intendedMove: "逼对方给出是否推进的明确态度"
    reply: "你先给我一句准话：这事你到底想不想成？"
  - exampleId: npc003_mask_insecurity_01
    npcId: npc_003
    situation: "别人指出他表现得过于着急"
    intendedMove: "否认自己的焦虑，并把压力转向团队迟迟不决"
    reply: "我急什么？我就是怕你们又商量半天，最后谁都不拍板。"
  - exampleId: npc003_deescalate_01
    npcId: npc_003
    situation: "激烈争论开始妨碍方案推进"
    intendedMove: "暂时停止争论，要求把条件逐项摆出来"
    reply: "行行行，先不争。条件一条条摆出来，能谈就谈。"
  - exampleId: npc003_close_01
    npcId: npc_003
    situation: "当天讨论已经结束但方案仍需整理"
    intendedMove: "主动承担整理工作并约定下一次交付"
    reply: "那今天先这样。方案我回去顺一遍，明天给你们看。"

  - exampleId: npc004_greet_01
    npcId: npc_004
    situation: "熟人站在门口没有进来"
    intendedMove: "用略带催促的关心招呼对方进屋"
    reply: "来了就别站门口，挡风。进来坐。"
  - exampleId: npc004_answer_help_01
    npcId: npc_004
    situation: "别人直接询问她是否愿意帮忙组织活动"
    intendedMove: "明确答应，同时要求安全事项由她把关"
    reply: "能帮，但我先说好，安全这块必须听我的。"
  - exampleId: npc004_refuse_privacy_01
    npcId: npc_004
    situation: "别人向她打听病人的私事"
    intendedMove: "直接拒绝并提醒对方不要继续打听"
    reply: "这个不能说，病人的事别打听。"
  - exampleId: npc004_disagree_safety_01
    npcId: npc_004
    situation: "有人为了省事准备跳过安全步骤"
    intendedMove: "明确反对，并要求对方正视责任"
    reply: "不行。你们想省这一步，出了问题谁负责？"
  - exampleId: npc004_probe_symptoms_01
    npcId: npc_004
    situation: "对方含糊描述自己的身体状况"
    intendedMove: "追问具体情况，不接受含糊回答"
    reply: "你到底哪儿不舒服？别跟我说‘还行’，说具体点。"
  - exampleId: npc004_worry_01
    npcId: npc_004
    situation: "对方状态明显不好却坚持说没事"
    intendedMove: "用直接提醒表达担心，阻止对方继续硬撑"
    reply: "你可别硬撑。脸色都这样了，还说没事。"
  - exampleId: npc004_soften_01
    npcId: npc_004
    situation: "她的话说重后需要缓和关系"
    intendedMove: "承认语气偏重，说明目的是把事情做稳"
    reply: "我话是重了点，但不是冲你。先把事情弄稳妥。"
  - exampleId: npc004_close_01
    npcId: npc_004
    situation: "讨论时间过长且大家已经疲惫"
    intendedMove: "果断结束讨论并催促需要休息的人离开"
    reply: "行了，今天就说到这儿。该休息的去休息。"

  - exampleId: npc005_greet_01
    npcId: npc_005
    situation: "熟人进入书店并靠近旧书"
    intendedMove: "简短确认对方到来，同时提醒不要碰书"
    reply: "来了。门边那摞书别碰。"
  - exampleId: npc005_answer_conditions_01
    npcId: npc_005
    situation: "别人询问他是否接受书店合作方案"
    intendedMove: "用最少的话明确接受条件"
    reply: "可以。旧书留下，账目公开。"
  - exampleId: npc005_refuse_past_01
    npcId: npc_005
    situation: "别人追问他不愿提起的过往"
    intendedMove: "直接划清边界，不解释原因"
    reply: "过去的事，不谈。"
  - exampleId: npc005_disagree_shop_01
    npcId: npc_005
    situation: "方案把书店当成可以随意改造的展示空间"
    intendedMove: "简短否定并重申书店不是装饰物"
    reply: "不妥。书店不是摆设。"
  - exampleId: npc005_probe_owner_01
    npcId: npc_005
    situation: "方案提出任务却没有明确负责人"
    intendedMove: "只追问承担责任的人"
    reply: "谁负责？"
  - exampleId: npc005_hesitate_01
    npcId: npc_005
    situation: "他需要时间考虑一项重要决定"
    intendedMove: "暂停表态，不给出虚假的即时承诺"
    reply: "……让我再想想。"
  - exampleId: npc005_deescalate_01
    npcId: npc_005
    situation: "众人争吵但没有形成可核对的条件"
    intendedMove: "停止口头争论，要求把条件写清"
    reply: "争没有用。把条件写下来。"
  - exampleId: npc005_close_01
    npcId: npc_005
    situation: "时间已晚且没有继续讨论的价值"
    intendedMove: "用最少的话结束当日谈话"
    reply: "天晚了。改日再说。"
```

- [ ] **Step 5: Run loader tests and the scenario integration test**

Run:

```powershell
python -m pytest test/backend/unit/test_scenario_loader.py test/backend/integration/test_scenario_api.py -q
```

Expected: PASS with 40 loaded examples, eight per NPC.

- [ ] **Step 6: Commit the scenario corpus**

```powershell
git add core/scenario/NPC_SPEECH_EXAMPLES.yaml core/backend/app/scenario/models.py core/backend/app/scenario/registry.py core/backend/app/scenario/loader.py test/backend/unit/test_scenario_loader.py
git commit -m "feat: add curated npc speech examples"
```

---

### Task 2: Implement the independent vector retriever

**Files:**

- Create: `core/backend/app/persistence/speech_example_retriever.py`
- Test: `test/backend/unit/test_speech_example_retriever.py`

**Interfaces:**

- Consumes: `ScenarioRegistry.speech_examples` from Task 1.
- Consumes: existing `EmbeddingPort.embed(text)` and optional provider `embed_many(texts)`.
- Produces: `SpeechExampleRetriever.search(*, npc_id: str, intent: str, limit: int = 3) -> SpeechExampleSearchResult`.
- Produces: `SpeechExampleHit(example, similarity)` and fail-open `failure_code` values.

- [ ] **Step 1: Write failing retriever tests with a deterministic embedding port**

Create `test/backend/unit/test_speech_example_retriever.py`:

```python
from __future__ import annotations

import pytest

from core.backend.app.ai.embedding import MEMORY_EMBEDDING_DIMENSIONS
from core.backend.app.persistence.speech_example_retriever import (
    VectorSpeechExampleRetriever,
)


def _vector(x: float, y: float) -> list[float]:
    return [x, y, *([0.0] * (MEMORY_EMBEDDING_DIMENSIONS - 2))]


class FakeEmbeddingPort:
    dimensions = MEMORY_EMBEDDING_DIMENSIONS
    model_name = "fake-speech-examples"

    def __init__(self, vectors: dict[str, list[float]], *, fail: bool = False) -> None:
        self.vectors = vectors
        self.fail = fail
        self.calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        if self.fail:
            raise RuntimeError("embedding unavailable")
        return self.vectors[text]

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(text) for text in texts]


@pytest.mark.anyio
async def test_intent_ranks_only_owned_examples(registry) -> None:
    intent = "委婉拒绝替玩家向别人说情，让玩家本人去沟通"
    owned = registry.speech_examples["npc001_refuse_mediate_01"]
    foreign = registry.speech_examples["npc003_refuse_bad_deal_01"]
    vectors = {
        intent: _vector(1.0, 0.0),
        VectorSpeechExampleRetriever.index_text(example): (
            _vector(1.0, 0.0)
            if example.example_id in {owned.example_id, foreign.example_id}
            else _vector(0.0, 1.0)
        )
        for example in registry.speech_examples.values()
    }
    port = FakeEmbeddingPort(vectors)
    retriever = VectorSpeechExampleRetriever(registry.speech_examples, port)

    result = await retriever.search(npc_id="npc_001", intent=intent, limit=3)

    assert result.failure_code is None
    assert result.hits[0].example.example_id == owned.example_id
    assert all(hit.example.npc_id == "npc_001" for hit in result.hits)
    assert len(result.hits) == 3


@pytest.mark.anyio
async def test_reply_text_is_never_embedded(registry) -> None:
    port = FakeEmbeddingPort({})
    retriever = VectorSpeechExampleRetriever(registry.speech_examples, port)
    expected = VectorSpeechExampleRetriever.index_text(
        registry.speech_examples["npc001_refuse_mediate_01"]
    )
    assert expected == (
        "情境：别人请求她代为向第三方说情\n"
        "回应方式：委婉拒绝代为沟通，把行动责任交还给请求者"
    )
    assert "这话我替你递不合适" not in expected


@pytest.mark.anyio
async def test_index_is_cached_after_first_search(registry) -> None:
    intent_one = "第一次查询"
    intent_two = "第二次查询"
    vectors = {
        intent_one: _vector(1.0, 0.0),
        intent_two: _vector(0.0, 1.0),
        **{
            VectorSpeechExampleRetriever.index_text(example): _vector(1.0, 0.0)
            for example in registry.speech_examples.values()
        },
    }
    port = FakeEmbeddingPort(vectors)
    retriever = VectorSpeechExampleRetriever(registry.speech_examples, port)
    await retriever.search(npc_id="npc_001", intent=intent_one)
    first_index_calls = len(port.calls) - 1
    await retriever.search(npc_id="npc_001", intent=intent_two)
    assert len(port.calls) == first_index_calls + 2


@pytest.mark.anyio
async def test_equal_scores_are_ordered_by_example_id(registry) -> None:
    intent = "相同分数"
    vectors = {
        intent: _vector(1.0, 0.0),
        **{
            VectorSpeechExampleRetriever.index_text(example): _vector(1.0, 0.0)
            for example in registry.speech_examples.values()
        },
    }
    result = await VectorSpeechExampleRetriever(
        registry.speech_examples, FakeEmbeddingPort(vectors)
    ).search(npc_id="npc_005", intent=intent, limit=3)
    assert [hit.example.example_id for hit in result.hits] == sorted(
        example.example_id
        for example in registry.speech_examples.values()
        if example.npc_id == "npc_005"
    )[:3]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("npc_id", "intent", "expected"),
    [
        ("npc_missing", "有效意图", "unknown_npc"),
        ("npc_001", "   ", "empty_intent"),
    ],
)
async def test_invalid_query_fails_open(registry, npc_id, intent, expected) -> None:
    retriever = VectorSpeechExampleRetriever(
        registry.speech_examples, FakeEmbeddingPort({})
    )
    result = await retriever.search(npc_id=npc_id, intent=intent)
    assert result.hits == ()
    assert result.failure_code == expected


@pytest.mark.anyio
async def test_embedding_failure_and_invalid_dimensions_fail_open(registry) -> None:
    failed = VectorSpeechExampleRetriever(
        registry.speech_examples, FakeEmbeddingPort({}, fail=True)
    )
    result = await failed.search(npc_id="npc_001", intent="拒绝请求")
    assert result.hits == ()
    assert result.failure_code == "embedding_error"

    bad_port = FakeEmbeddingPort({"拒绝请求": [1.0]})
    bad = VectorSpeechExampleRetriever(registry.speech_examples, bad_port)
    result = await bad.search(npc_id="npc_001", intent="拒绝请求")
    assert result.hits == ()
    assert result.failure_code == "invalid_vector"
```

- [ ] **Step 2: Run the new tests and verify failure**

Run:

```powershell
python -m pytest test/backend/unit/test_speech_example_retriever.py -q
```

Expected: collection FAIL because `speech_example_retriever.py` does not exist.

- [ ] **Step 3: Implement result types, vector validation, cache, and ranking**

Create `core/backend/app/persistence/speech_example_retriever.py` with these public contracts and behavior:

```python
from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from ..ai.embedding import MEMORY_EMBEDDING_DIMENSIONS, EmbeddingPort
from ..scenario.models import SpeechExampleDefinition


@dataclass(frozen=True, slots=True)
class SpeechExampleHit:
    example: SpeechExampleDefinition
    similarity: float


@dataclass(frozen=True, slots=True)
class SpeechExampleSearchResult:
    hits: tuple[SpeechExampleHit, ...] = ()
    failure_code: str | None = None


class SpeechExampleRetriever(Protocol):
    async def search(
        self, *, npc_id: str, intent: str, limit: int = 3
    ) -> SpeechExampleSearchResult: ...


class VectorSpeechExampleRetriever:
    def __init__(
        self,
        examples: Mapping[str, SpeechExampleDefinition],
        embedding_port: EmbeddingPort,
        *,
        dimensions: int = MEMORY_EMBEDDING_DIMENSIONS,
    ) -> None:
        if dimensions != MEMORY_EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"speech example retriever requires {MEMORY_EMBEDDING_DIMENSIONS}-dimension vectors"
            )
        self._examples = examples
        self._embedding_port = embedding_port
        self._dimensions = dimensions
        self._index: dict[str, tuple[float, ...]] | None = None
        self._index_lock = asyncio.Lock()

    @staticmethod
    def index_text(example: SpeechExampleDefinition) -> str:
        return f"情境：{example.situation}\n回应方式：{example.intended_move}"

    def _vector(self, values: Sequence[float]) -> tuple[float, ...] | None:
        try:
            vector = tuple(float(value) for value in values)
        except (TypeError, ValueError):
            return None
        if len(vector) != self._dimensions or not all(math.isfinite(value) for value in vector):
            return None
        return vector

    async def _embed_many(self, texts: list[str]) -> Sequence[Sequence[float]]:
        embed_many = getattr(self._embedding_port, "embed_many", None)
        if callable(embed_many):
            return await embed_many(texts)
        return [await self._embedding_port.embed(text) for text in texts]

    async def _ensure_index(self) -> str | None:
        if self._index is not None:
            return None
        async with self._index_lock:
            if self._index is not None:
                return None
            ordered = sorted(self._examples.values(), key=lambda item: item.example_id)
            texts = [self.index_text(example) for example in ordered]
            try:
                raw_vectors = await self._embed_many(texts)
            except Exception:
                return "embedding_error"
            if len(raw_vectors) != len(ordered):
                return "invalid_vector"
            parsed = [self._vector(vector) for vector in raw_vectors]
            if any(vector is None for vector in parsed):
                return "invalid_vector"
            self._index = {
                example.example_id: vector
                for example, vector in zip(ordered, parsed, strict=True)
                if vector is not None
            }
        return None

    @staticmethod
    def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
        numerator = sum(a * b for a, b in zip(left, right, strict=True))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return numerator / (left_norm * right_norm)

    async def search(
        self, *, npc_id: str, intent: str, limit: int = 3
    ) -> SpeechExampleSearchResult:
        if limit <= 0:
            raise ValueError("limit must be greater than zero")
        owned = [
            example for example in self._examples.values() if example.npc_id == npc_id
        ]
        if not owned:
            return SpeechExampleSearchResult(failure_code="unknown_npc")
        query = intent.strip()
        if not query:
            return SpeechExampleSearchResult(failure_code="empty_intent")
        failure = await self._ensure_index()
        if failure is not None:
            return SpeechExampleSearchResult(failure_code=failure)
        try:
            query_vector = self._vector(await self._embedding_port.embed(query))
        except Exception:
            return SpeechExampleSearchResult(failure_code="embedding_error")
        if query_vector is None or self._index is None:
            return SpeechExampleSearchResult(failure_code="invalid_vector")
        ranked = [
            SpeechExampleHit(
                example=example,
                similarity=self._cosine(query_vector, self._index[example.example_id]),
            )
            for example in owned
        ]
        ranked.sort(key=lambda hit: (-hit.similarity, hit.example.example_id))
        return SpeechExampleSearchResult(hits=tuple(ranked[:limit]))


__all__ = [
    "SpeechExampleHit",
    "SpeechExampleRetriever",
    "SpeechExampleSearchResult",
    "VectorSpeechExampleRetriever",
]
```

- [ ] **Step 4: Run retriever tests and lint the new module**

Run:

```powershell
python -m pytest test/backend/unit/test_speech_example_retriever.py -q
python -m ruff check core/backend/app/persistence/speech_example_retriever.py test/backend/unit/test_speech_example_retriever.py
```

Expected: all tests PASS and Ruff reports no violations.

- [ ] **Step 5: Commit the retriever**

```powershell
git add core/backend/app/persistence/speech_example_retriever.py test/backend/unit/test_speech_example_retriever.py
git commit -m "feat: retrieve npc speech examples by intent"
```

---

### Task 3: Wire the retriever into application lifecycle

**Files:**

- Modify: `core/backend/app/main.py`
- Modify: `.env.example`
- Test: `test/backend/unit/test_settings.py`
- Test: `test/backend/integration/test_health_api.py`

**Interfaces:**

- Consumes: `VectorSpeechExampleRetriever(registry.speech_examples, embedding_port)` from Task 2.
- Produces: `application.state.speech_example_retriever`.
- Produces: `RunService(..., speech_example_retriever=...)` for Task 4.

- [ ] **Step 1: Write a failing lifecycle test**

Add these imports, fake, and test to `test/backend/integration/test_health_api.py`:

```python
from pathlib import Path

from fastapi.testclient import TestClient

import core.backend.app.main as main_module
from core.backend.app.main import create_app
from core.backend.app.settings import Settings


class FakeEmbeddingClient:
    dimensions = 2048
    model_name = "fake-embedding"

    async def embed(self, _text: str) -> list[float]:
        return [1.0, *([0.0] * 2047)]

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(text) for text in texts]

    async def close(self) -> None:
        return None


def test_memory_backend_wires_speech_example_retriever(
    monkeypatch,
) -> None:
    scenario_dir = Path(__file__).resolve().parents[3] / "core" / "scenario"
    fake_embedding = FakeEmbeddingClient()
    monkeypatch.setattr(
        main_module,
        "ArkEmbeddingClient",
        lambda _settings: fake_embedding,
    )
    settings = Settings(
        scenario_dir=scenario_dir,
        persistence_backend="memory",
        embedding_model="fake-embedding",
    )
    with TestClient(create_app(settings)) as client:
        assert client.app.state.embedding_client is fake_embedding
        assert client.app.state.speech_example_retriever is not None
        assert (
            client.app.state.run_service.speech_example_retriever
            is client.app.state.speech_example_retriever
        )
```

The fake embedding object must expose `dimensions = 2048`, `model_name`, async `embed`, async `embed_many`, and async `close`, without making network requests.

- [ ] **Step 2: Run the focused lifecycle test and verify failure**

Run:

```powershell
python -m pytest test/backend/integration/test_health_api.py -q
```

Expected: FAIL because the embedding client is currently constructed only inside the PostgreSQL branch and `RunService` has no speech-example dependency.

- [ ] **Step 3: Refactor embedding construction and inject the retriever**

In `core/backend/app/main.py`, construct `embedding_port` once before choosing the persistence backend:

```python
application.state.embedding_client = None
application.state.embedding_indexer = None
application.state.speech_example_retriever = None
embedding_port = None
if runtime_settings.embedding_model:
    embedding_settings = (
        ArkEmbeddingSettings(
            model=runtime_settings.embedding_model,
            base_url=runtime_settings.embedding_base_url,
        )
        if runtime_settings.embedding_base_url
        else ArkEmbeddingSettings(model=runtime_settings.embedding_model)
    )
    application.state.embedding_client = ArkEmbeddingClient(embedding_settings)
    embedding_port = application.state.embedding_client
    application.state.speech_example_retriever = VectorSpeechExampleRetriever(
        registry.speech_examples,
        embedding_port,
        dimensions=runtime_settings.memory_embedding_dimensions,
    )
```

Leave `MemoryEmbeddingIndexer` and `DatabaseMemoryRetriever` creation inside the PostgreSQL branch, reusing the already-created `embedding_port`. Pass the retriever into `RunService`:

```python
speech_example_retriever=application.state.speech_example_retriever,
```

In `core/backend/app/orchestration/run_service.py`, add the constructor parameter and property without using it yet:

```python
speech_example_retriever: SpeechExampleRetriever | None = None,

self.speech_example_retriever = speech_example_retriever
```

- [ ] **Step 4: Document the shared embedding configuration**

In `.env.example`, update the `ARK_EMBEDDING_MODEL` comment to state exactly:

```dotenv
# Enables 2048-dimensional vectors for PostgreSQL private-memory retrieval and
# process-local NPC speech-example retrieval. When unset, speech generation
# continues without dynamically selected examples.
ARK_EMBEDDING_MODEL=
```

- [ ] **Step 5: Run lifecycle, settings, and health tests**

Run:

```powershell
python -m pytest test/backend/unit/test_settings.py test/backend/integration/test_health_api.py -q
```

Expected: PASS; memory persistence can instantiate the speech retriever when an embedding model is configured, and no external provider is called during startup.

- [ ] **Step 6: Commit dependency injection**

```powershell
git add core/backend/app/main.py core/backend/app/orchestration/run_service.py .env.example test/backend/integration/test_health_api.py
git commit -m "feat: wire speech example retrieval"
```

---

### Task 4: Retrieve after final decisions and inject normal-round prompts

**Files:**

- Modify: `core/backend/app/orchestration/run_service.py`
- Test: `test/backend/unit/test_message_driven_chat_rounds.py`

**Interfaces:**

- Consumes: optional `SpeechExampleRetriever` from Task 3.
- Produces: `_retrieve_speech_examples(npc_id, intent) -> SpeechExampleSearchResult`.
- Produces: `_speech_examples_payload(result) -> list[dict[str, str]]` containing only `situation`, `intendedMove`, and `reply`.
- Preserves: `_generate_one_speech(npc_id, decision, prompt)` and existing publication behavior.

- [ ] **Step 1: Add a recording fake and failing normal-round tests**

Add to `test/backend/unit/test_message_driven_chat_rounds.py`:

```python
from core.backend.app.persistence.speech_example_retriever import (
    SpeechExampleHit,
    SpeechExampleSearchResult,
)


class RecordingSpeechExampleRetriever:
    def __init__(self, registry, *, failure_code: str | None = None) -> None:
        self.registry = registry
        self.failure_code = failure_code
        self.calls: list[tuple[str, str, int]] = []

    async def search(self, *, npc_id: str, intent: str, limit: int = 3):
        self.calls.append((npc_id, intent, limit))
        if self.failure_code is not None:
            return SpeechExampleSearchResult(failure_code=self.failure_code)
        examples = [
            example
            for example in self.registry.speech_examples.values()
            if example.npc_id == npc_id
        ][:limit]
        return SpeechExampleSearchResult(
            hits=tuple(
                SpeechExampleHit(example=example, similarity=1.0 - index * 0.1)
                for index, example in enumerate(examples)
            )
        )
```

Add three async tests:

```python
@pytest.mark.anyio
async def test_final_speak_intent_retrieves_examples_before_speech(registry) -> None:
    model = ParallelRoundModel(speak_calls=1)
    retriever = RecordingSpeechExampleRetriever(registry)
    service = RunService(
        registry,
        text_model=model,
        speech_example_retriever=retriever,
        chat_cooldown_seconds=10,
    )
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    async with run.lock:
        conversation = _install_conversation(
            service,
            run,
            [registry.player_actor_id, "npc_001"],
        )
        await service.repository.save(run)
    await service.player_message(
        run.run_id,
        conversation.conversation_id,
        "林老师，您替我跟周老板说说吧。",
    )
    await service.wait_for_chat_idle(run.run_id, conversation.conversation_id)

    assert retriever.calls == [("npc_001", "回应玩家刚才的发言", 3)]
    speech_request = next(
        request for request in model.requests if "协议=SpeechGeneration" in request.system_prompt
    )
    payload = json.loads(speech_request.messages[0].content)
    assert len(payload["context"]["speechExamples"]) == 3
    assert set(payload["context"]["speechExamples"][0]) == {
        "situation",
        "intendedMove",
        "reply",
    }
    assert "exampleId" not in speech_request.messages[0].content
    assert "similarity" not in speech_request.messages[0].content
    await service.close()


@pytest.mark.anyio
async def test_final_wait_does_not_retrieve_examples(registry) -> None:
    model = ParallelRoundModel(speak_calls=0)
    retriever = RecordingSpeechExampleRetriever(registry)
    service = RunService(
        registry,
        text_model=model,
        speech_example_retriever=retriever,
        chat_cooldown_seconds=10,
    )
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    async with run.lock:
        conversation = _install_conversation(
            service,
            run,
            [registry.player_actor_id, "npc_001"],
        )
        await service.repository.save(run)
    await service.player_message(run.run_id, conversation.conversation_id, "先不聊了。")
    await service.wait_for_chat_idle(run.run_id, conversation.conversation_id)
    assert retriever.calls == []
    assert model.speech_calls == 0
    await service.close()


@pytest.mark.anyio
async def test_example_retrieval_failure_keeps_speech_generation(registry) -> None:
    model = ParallelRoundModel(speak_calls=1)
    retriever = RecordingSpeechExampleRetriever(
        registry, failure_code="embedding_error"
    )
    service = RunService(
        registry,
        text_model=model,
        speech_example_retriever=retriever,
        chat_cooldown_seconds=10,
    )
    created = await service.create_run()
    run = await service.get_run_entity(created["runId"])
    async with run.lock:
        conversation = _install_conversation(
            service,
            run,
            [registry.player_actor_id, "npc_001"],
        )
        await service.repository.save(run)
    await service.player_message(run.run_id, conversation.conversation_id, "你怎么看？")
    await service.wait_for_chat_idle(run.run_id, conversation.conversation_id)
    speech_request = next(
        request for request in model.requests if "协议=SpeechGeneration" in request.system_prompt
    )
    payload = json.loads(speech_request.messages[0].content)
    assert payload["context"]["speechExamples"] == []
    assert model.speech_calls == 1
    assert any(
        message["authorActorId"] == "npc_001"
        for message in run.messages[conversation.conversation_id]
    )
    await service.close()
```

Extend `ParallelRoundModel` with `self.requests: list[Any]` and append each request at the beginning of `generate` so prompt shape can be asserted without a second test model.

- [ ] **Step 2: Run the focused round tests and verify failure**

Run:

```powershell
python -m pytest test/backend/unit/test_message_driven_chat_rounds.py -k "speech_intent or example_retrieval" -q
```

Expected: FAIL because `RunService` does not call the retriever or inject `speechExamples`.

- [ ] **Step 3: Add fail-open retrieval and safe payload projection**

In `RunService`, implement:

```python
async def _retrieve_speech_examples(
    self,
    npc_id: str,
    intent: str | None,
) -> SpeechExampleSearchResult:
    if self.speech_example_retriever is None:
        return SpeechExampleSearchResult(failure_code="disabled")
    try:
        return await self.speech_example_retriever.search(
            npc_id=npc_id,
            intent=intent or "",
            limit=3,
        )
    except Exception:
        return SpeechExampleSearchResult(failure_code="retriever_error")

@staticmethod
def _speech_examples_payload(
    result: SpeechExampleSearchResult,
) -> list[dict[str, str]]:
    return [
        {
            "situation": hit.example.situation,
            "intendedMove": hit.example.intended_move,
            "reply": hit.example.reply,
        }
        for hit in result.hits
    ]
```

Log one structured INFO record after every attempted search with `npc_id`, result count, example IDs, rounded similarities, duration, and `failure_code`; do not log `intent`, replies, private Memory, or the full prompt.

- [ ] **Step 4: Move retrieval outside `Run.lock` in `_execute_message_round`**

Restructure the current candidate-to-prompt block into three phases:

```python
# Phase A, under Run.lock: validate decisions and freeze ordered candidates.
ordered = self._order_speakers(...)
state["status"] = "generating"
state["pendingLeaverIds"] = leaving_ids
state["pendingPostSpeechLeaverIds"] = [
    npc_id for npc_id, decision in ordered if decision.leave_chat_after_speaking
]
await self.repository.save(run)

# Phase B, without Run.lock: retrieve examples concurrently.
example_results = await asyncio.gather(
    *(
        self._retrieve_speech_examples(npc_id, decision.intent)
        for npc_id, decision in ordered
    )
)

# Phase C, under Run.lock: revalidate the frozen round and build prompts.
if not self._round_still_current_locked(...):
    return
prompts = [
    (
        npc_id,
        decision,
        self._npc_prompt(
            run,
            npc_id,
            "speech",
            {
                "conversationId": conversation_id,
                "roundId": round_id,
                "replyToMessageIds": trigger_message_ids,
                "intent": decision.intent,
                "speechExamples": self._speech_examples_payload(example_result),
                **self._chat_context(run, conversation, npc_id),
            },
        ),
    )
    for (npc_id, decision), example_result in zip(
        ordered, example_results, strict=True
    )
]
run.in_flight_speech_calls += len(prompts)
await self.repository.save(run)
```

Do not add a new persisted round status; existing `generating` covers example retrieval plus SpeechGeneration. Preserve the existing finally block, stale checks, in-flight model-call accounting, speaker order, duplicate suppression, publication, and cooldown behavior.

- [ ] **Step 5: Run normal-round, recovery, and failure suites**

Run:

```powershell
python -m pytest test/backend/unit/test_message_driven_chat_rounds.py test/backend/unit/test_inflight_day_end.py -q
```

Expected: PASS, including existing deciding/generating restart tests and the new fail-open cases.

- [ ] **Step 6: Commit normal-round integration**

```powershell
git add core/backend/app/orchestration/run_service.py test/backend/unit/test_message_driven_chat_rounds.py
git commit -m "feat: inject examples into npc round speech"
```

---

### Task 5: Cover opener and join speech paths

**Files:**

- Modify: `core/backend/app/orchestration/run_service.py`
- Test: `test/backend/unit/test_join_requests.py`

**Interfaces:**

- Consumes: `_retrieve_speech_examples` and `_speech_examples_payload` from Task 4.
- Produces: identical example behavior for `conversation_opener` and `join_opener` without changing visibility or participant-version rules.

- [ ] **Step 1: Write failing opener and join tests**

Add `speech_contexts`, `requests`, and `speak_on_chat` to `JoinDecisionModel`. When `speak_on_chat` is true, its ChatDecision response must be:

```python
{
    "result": "decided",
    "action": "speak",
    "responseDesire": 3,
    "intent": f"{actor_id} 根据当前加入场景作出简短回应",
}
```

Its SpeechGeneration branch must append the decoded payload to `speech_contexts` and return `{"text": f"{actor_id} 的开场。"}`. Add this local retriever:

```python
class RecordingSpeechExampleRetriever:
    def __init__(self, registry) -> None:
        self.registry = registry
        self.calls: list[tuple[str, str, int]] = []

    async def search(self, *, npc_id: str, intent: str, limit: int = 3):
        self.calls.append((npc_id, intent, limit))
        examples = [
            example
            for example in self.registry.speech_examples.values()
            if example.npc_id == npc_id
        ][:limit]
        return SpeechExampleSearchResult(
            hits=tuple(
                SpeechExampleHit(example=example, similarity=1.0)
                for example in examples
            )
        )
```

Then add these complete setup and assertions:

```python
@pytest.mark.anyio
async def test_npc_conversation_opener_retrieves_from_final_intent(registry) -> None:
    model = JoinDecisionModel()
    model.speak_on_chat = True
    retriever = RecordingSpeechExampleRetriever(registry)
    service = RunService(
        registry,
        text_model=model,
        speech_example_retriever=retriever,
        chat_cooldown_seconds=10,
    )
    created = await service.create_run()
    opened = await service.create_conversation(
        created["runId"], ["npc_001", "npc_002"]
    )
    await service.wait_for_chat_idle(
        created["runId"], opened["conversation"]["conversationId"]
    )
    npc_id, intent, limit = retriever.calls[0]
    assert npc_id in {"npc_001", "npc_002"}
    assert intent == f"{npc_id} 根据当前加入场景作出简短回应"
    assert limit == 3
    examples = model.speech_contexts[0]["context"]["speechExamples"]
    assert len(examples) == 3
    assert all(
        example["reply"]
        in {
            item.reply
            for item in registry.speech_examples.values()
            if item.npc_id == npc_id
        }
        for example in examples
    )
    await service.close()


@pytest.mark.anyio
async def test_npc_join_opener_retrieves_without_prejoin_message_access(registry) -> None:
    model = JoinDecisionModel()
    model.speak_on_chat = True
    retriever = RecordingSpeechExampleRetriever(registry)
    service = RunService(
        registry,
        text_model=model,
        speech_example_retriever=retriever,
        chat_cooldown_seconds=10,
    )
    created, run, conversation_id = await _two_npc_chat(service)
    await service.wait_for_chat_idle(created["runId"], conversation_id)
    retriever.calls.clear()
    model.speech_contexts.clear()
    conversation = run.conversations[conversation_id]
    async with run.lock:
        service._write_message_locked(run, conversation, "npc_001", "加入前的旧话")
    result = await service.add_participant(
        created["runId"], conversation_id, "npc_003", "join-with-examples"
    )
    await service.wait_for_chat_idle(created["runId"], conversation_id)
    assert result["joinRequest"]["status"] == "accepted"
    assert retriever.calls == [
        ("npc_003", "npc_003 根据当前加入场景作出简短回应", 3)
    ]
    assert model.speech_contexts[0]["context"]["messages"] == []
    assert all(
        example["reply"]
        in {
            item.reply
            for item in registry.speech_examples.values()
            if item.npc_id == "npc_003"
        }
        for example in model.speech_contexts[0]["context"]["speechExamples"]
    )
    await service.close()
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```powershell
python -m pytest test/backend/unit/test_join_requests.py -k "retrieves" -q
```

Expected: FAIL because `_execute_opener_round` currently builds its speech prompt before example retrieval.

- [ ] **Step 3: Add the same unlock/revalidate pattern to `_execute_opener_round`**

After the final opener decision is accepted:

1. Under `Run.lock`, apply recalled memory IDs and drafts, set `state["status"] = "generating"`, and save the run.
2. Release `Run.lock` and call `_retrieve_speech_examples(npc_id, decision.intent or state_opener_intent)` once.
3. Reacquire `Run.lock`, call `_round_still_current_locked`, and return if stale.
4. Build the existing `opening_speech` or `join_speech` prompt with:

```python
"speechExamples": self._speech_examples_payload(example_result),
```

5. Increment `in_flight_speech_calls` only immediately before the real SpeechGeneration call.
6. Preserve the existing empty-speech cooldown, publication, participant visibility, and final boundary handling.

- [ ] **Step 4: Run join, opener, visibility, and day-boundary tests**

Run:

```powershell
python -m pytest test/backend/unit/test_join_requests.py test/backend/unit/test_inflight_day_end.py test/backend/unit/test_d065_segment_compression.py -q
```

Expected: PASS with no pre-join message leakage and no stale opener publication.

- [ ] **Step 5: Commit opener integration**

```powershell
git add core/backend/app/orchestration/run_service.py test/backend/unit/test_join_requests.py
git commit -m "feat: retrieve examples for npc openers"
```

---

### Task 6: Tighten intent and SpeechGeneration contracts

**Files:**

- Modify: `core/backend/app/ai/decision_service.py`
- Test: `test/backend/unit/test_decision_service_json.py`
- Test: `test/backend/unit/test_message_driven_chat_rounds.py`

**Interfaces:**

- Consumes: `context.speechExamples` inserted by Tasks 4 and 5.
- Produces: sufficiently specific `ChatDecision.intent` for vector retrieval.
- Produces: explicit few-shot usage rules that prohibit copying example facts or full sentences.

- [ ] **Step 1: Write failing protocol-rule assertions**

Add to `test/backend/unit/test_decision_service_json.py`:

```python
def test_chat_decision_requires_specific_speech_intent() -> None:
    rule = PROTOCOL_RULES["ChatDecision"]
    assert "回应对象" in rule
    assert "对话动作" in rule
    assert "自然回应" in rule


def test_speech_examples_are_style_only() -> None:
    rule = PROTOCOL_RULES["SpeechGeneration"]
    assert "speechExamples" in rule
    assert "不得照抄" in rule
    assert "不能作为世界事实" in rule
    assert "当前上下文" in rule
```

Add one prompt-capture assertion to the normal-round test from Task 4 proving current visible messages remain in `context.messages` while the embedding fake receives only final intent.

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```powershell
python -m pytest test/backend/unit/test_decision_service_json.py test/backend/unit/test_message_driven_chat_rounds.py -k "intent or style_only or visible_messages" -q
```

Expected: FAIL because the rules do not mention the new intent or example contract.

- [ ] **Step 3: Add positive, bounded protocol instructions**

Append to `PROTOCOL_RULES["ChatDecision"]`:

```python
PROTOCOL_RULES["ChatDecision"] += (
    "当最终 action=speak 时，intent 必须具体说明回应对象、正在回应的事情、"
    "回答/拒绝/追问/质疑/安慰/妥协/告别等对话动作、角色立场，以及必要的玩家选择空间；"
    "不得只写‘自然回应’、‘继续对话’或其他无法区分回应方式的空泛描述。"
)
```

Append to `PROTOCOL_RULES["SpeechGeneration"]`:

```python
PROTOCOL_RULES["SpeechGeneration"] += (
    "context.speechExamples 是当前角色在相似情境中的表达示范，只用于学习语气、"
    "节奏、措辞密度和处理方式。必须结合当前上下文与本次 intent 重新作答，不得照抄"
    "示例中的完整句子、人物、事实或承诺；示例不能作为世界事实、Memory 或关系证据。"
    "当前上下文与示例冲突时，以当前可见消息、角色边界和本次 intent 为准。"
)
```

Do not change the `SpeechGeneration` schema, maximum text length, JSON-only protocol, temperature, or retry behavior.

- [ ] **Step 4: Run decision, round, and evaluation-runner tests**

Run:

```powershell
python -m pytest test/backend/unit/test_decision_service_json.py test/backend/unit/test_message_driven_chat_rounds.py test/backend/unit/test_evaluation_runner.py -q
```

Expected: PASS; existing structured protocol behavior remains valid.

- [ ] **Step 5: Commit protocol guidance**

```powershell
git add core/backend/app/ai/decision_service.py test/backend/unit/test_decision_service_json.py test/backend/unit/test_message_driven_chat_rounds.py
git commit -m "feat: guide intent-driven speech examples"
```

---

### Task 7: Add end-to-end regression coverage and documentation

**Files:**

- Modify: `test/backend/integration/test_playable_loop_smoke.py`
- Modify: `README.md`
- Modify: `project/SPEECH_EXAMPLE_DYNAMIC_RETRIEVAL_DESIGN.md`

**Interfaces:**

- Verifies the complete final-decision → example retrieval → SpeechGeneration path.
- Documents operational enablement and the no-embedding fail-open behavior.

- [ ] **Step 1: Add a failing playable-loop assertion**

Extend `ScriptedModel` with `self.requests: list[object]`, initialize it in `__init__`, and append every request at the start of `generate`. Add this fake and integration test:

```python
from core.backend.app.persistence.speech_example_retriever import (
    SpeechExampleHit,
    SpeechExampleSearchResult,
)


class OneExampleRetriever:
    def __init__(self, registry) -> None:
        self.registry = registry
        self.calls: list[tuple[str, str, int]] = []

    async def search(self, *, npc_id: str, intent: str, limit: int = 3):
        self.calls.append((npc_id, intent, limit))
        example = next(
            item
            for item in self.registry.speech_examples.values()
            if item.npc_id == npc_id
        )
        return SpeechExampleSearchResult(
            hits=(SpeechExampleHit(example=example, similarity=1.0),)
        )


@pytest.mark.anyio
async def test_playable_opener_injects_intent_selected_example(registry) -> None:
    model = ScriptedModel()
    retriever = OneExampleRetriever(registry)
    service = RunService(
        registry,
        text_model=model,
        speech_example_retriever=retriever,
        seed=1,
        chat_publish_delay_min_seconds=0,
        chat_publish_delay_max_seconds=0,
    )
    created = await service.create_run()
    await service.world_step(created["runId"], 240)
    run = await service.get_run_entity(created["runId"])
    for conversation_id in tuple(run.conversations):
        await service.wait_for_chat_idle(run.run_id, conversation_id)
    assert len(retriever.calls) >= 1
    speaking_npc_id, final_intent, limit = retriever.calls[0]
    assert final_intent
    assert limit == 3
    speech_request = next(
        request
        for request in model.requests
        if "协议=SpeechGeneration" in request.system_prompt
    )
    speech_payload = json.loads(speech_request.messages[0].content)
    expected_example = next(
        item
        for item in registry.speech_examples.values()
        if item.npc_id == speaking_npc_id
    )
    assert speech_payload["context"]["speechExamples"] == [
        {
            "situation": expected_example.situation,
            "intendedMove": expected_example.intended_move,
            "reply": expected_example.reply,
        }
    ]
    await service.close()


@pytest.mark.anyio
async def test_playable_opener_without_retriever_keeps_speaking(registry) -> None:
    model = ScriptedModel()
    service = RunService(
        registry,
        text_model=model,
        speech_example_retriever=None,
        seed=1,
        chat_publish_delay_min_seconds=0,
        chat_publish_delay_max_seconds=0,
    )
    created = await service.create_run()
    await service.world_step(created["runId"], 240)
    run = await service.get_run_entity(created["runId"])
    for conversation_id in tuple(run.conversations):
        await service.wait_for_chat_idle(run.run_id, conversation_id)
    speech_request = next(
        request
        for request in model.requests
        if "协议=SpeechGeneration" in request.system_prompt
    )
    speech_payload = json.loads(speech_request.messages[0].content)
    assert speech_payload["context"]["speechExamples"] == []
    assert any(
        message.get("authorActorId", "").startswith("npc_")
        for messages in run.messages.values()
        for message in messages
    )
    await service.close()
```

- [ ] **Step 2: Run the integration test and verify failure before final wiring**

Run:

```powershell
python -m pytest test/backend/integration/test_playable_loop_smoke.py -q
```

Expected before final wiring: FAIL on the missing example payload. Expected after Tasks 1–6: PASS.

- [ ] **Step 3: Update operational documentation**

Add a concise README section covering:

```markdown
### NPC 示例对白检索

NPC 在最终决定发言后，以最终 `intent` 在本角色的人工示例对白中执行 Top 3
向量检索，并把结果交给 SpeechGeneration。当前轮消息仍作为生成上下文，但不进入
首版检索向量。设置 `ARK_EMBEDDING_MODEL` 后启用；未设置或 embedding 暂时失败时，
系统不召回示例但仍正常生成台词。示例语料位于
`core/scenario/NPC_SPEECH_EXAMPLES.yaml`。
```

Change the design document status line to:

```markdown
- 状态：首版方案已确认，实施计划已完成，待执行
```

- [ ] **Step 4: Run the complete backend verification suite**

Run from the repository root:

```powershell
python -m pytest test/backend -q
python -m ruff check core/backend/app test/backend
python -m mypy core/backend/app
```

Expected:

- Pytest exits 0 with no failed tests.
- Ruff exits 0 with no diagnostics.
- Mypy exits 0 with no type errors.

- [ ] **Step 5: Run the frontend regression suite because backend event timing changed internally**

Run:

```powershell
Set-Location core/frontend
pnpm test -- --run
```

Expected: all frontend unit tests PASS; no public event or payload contract changed.

- [ ] **Step 6: Review the diff for scope and secrets**

Run:

```powershell
git status --short
git diff --check
git diff --stat
git diff -- . ':!.env'
```

Expected:

- No `.env`, provider key, production conversation, generated vector, or private runtime state is staged.
- No Alembic migration, frontend source, MemoryRetriever, LangGraph route, or persistence codec change appears.
- `git diff --check` exits 0.

- [ ] **Step 7: Commit the complete feature documentation and regression coverage**

```powershell
git add README.md project/SPEECH_EXAMPLE_DYNAMIC_RETRIEVAL_DESIGN.md test/backend/integration/test_playable_loop_smoke.py
git commit -m "test: verify intent-driven speech examples"
```

---

## Final Acceptance Checklist

- [ ] The canonical scenario loads 40 reviewed examples, exactly eight for each of five NPCs.
- [ ] Duplicate IDs, unknown NPC IDs, empty fields, and fewer than eight examples per NPC fail scenario startup with file/field context.
- [ ] Only `situation + intendedMove` is embedded; `reply` is never embedded.
- [ ] Only final `action=speak` decisions trigger retrieval.
- [ ] A `need_memory` decision triggers only the existing Memory tool; example retrieval happens once only if the post-recall decision speaks.
- [ ] Each query embeds only final `intent`.
- [ ] Results are hard-scoped to the current NPC, deterministic, deduplicated, and limited to three.
- [ ] Example retrieval happens outside `Run.lock` and outside the model semaphore.
- [ ] Normal-round, conversation-opener, and join-opener speech all receive the same example payload shape.
- [ ] Current visible messages remain in SpeechGeneration context and are not used as the v1 query vector.
- [ ] IDs, scores, vectors, and error codes never enter the model prompt.
- [ ] Embedding or retriever failures still permit normal SpeechGeneration and publication.
- [ ] Existing visibility, stale-round, recovery, day-boundary, memory ownership, draft, and publication-order tests pass.
- [ ] Backend pytest, Ruff, Mypy, and frontend unit tests all pass.
