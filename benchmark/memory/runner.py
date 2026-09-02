"""Injectable runner for Memory/RAG ablation experiments."""

from __future__ import annotations

import inspect
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from .dataset import (
    MemoryBenchmarkDataset,
    MemoryQueryCase,
    MemoryQuerySpec,
    load_memory_dataset,
)
from .scorer import aggregate_observations, compute_paired_effects, score_retrieval

ConfigId = Literal[
    "R0_full_hybrid",
    "R1_keyword_only",
    "R2_vector_only",
    "R3_no_graph",
    "R4_no_owner_guard",
]


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    """A named retrieval ablation with explicit capability switches."""

    config_id: str = "R0_full_hybrid"
    use_keyword: bool | None = None
    use_vector: bool | None = None
    use_structured_filter: bool | None = None
    use_graph: bool | None = None
    use_rerank: bool | None = None
    use_owner_guard: bool | None = None

    def __post_init__(self) -> None:
        presets: dict[str, dict[str, bool]] = {
            "R0_full_hybrid": {
                "use_keyword": True,
                "use_vector": True,
                "use_structured_filter": True,
                "use_graph": True,
                "use_rerank": True,
                "use_owner_guard": True,
            },
            "R1_keyword_only": {
                "use_keyword": True,
                "use_vector": False,
                "use_structured_filter": True,
                "use_graph": False,
                "use_rerank": False,
                "use_owner_guard": True,
            },
            "R2_vector_only": {
                "use_keyword": False,
                "use_vector": True,
                "use_structured_filter": False,
                "use_graph": False,
                "use_rerank": False,
                "use_owner_guard": True,
            },
            "R3_no_graph": {
                "use_keyword": True,
                "use_vector": True,
                "use_structured_filter": True,
                "use_graph": False,
                "use_rerank": True,
                "use_owner_guard": True,
            },
            "R4_no_owner_guard": {
                "use_keyword": True,
                "use_vector": True,
                "use_structured_filter": True,
                "use_graph": True,
                "use_rerank": True,
                "use_owner_guard": False,
            },
        }
        if self.config_id not in presets:
            raise ValueError(
                f"unsupported retrieval config {self.config_id!r}; "
                f"choose one of {tuple(presets)}"
            )
        values = presets[self.config_id]
        for field_name, default in values.items():
            if getattr(self, field_name) is None:
                object.__setattr__(self, field_name, default)

    @property
    def id(self) -> str:
        return self.config_id

    @property
    def name(self) -> str:
        return self.config_id

    @property
    def ablation_id(self) -> str:
        return self.config_id

    def as_dict(self) -> dict[str, Any]:
        return {
            "configId": self.config_id,
            "useKeyword": self.use_keyword,
            "useVector": self.use_vector,
            "useStructuredFilter": self.use_structured_filter,
            "useGraph": self.use_graph,
            "useRerank": self.use_rerank,
            "useOwnerGuard": self.use_owner_guard,
        }


AblationConfig = RetrievalConfig
DEFAULT_CONFIGS: Mapping[str, RetrievalConfig] = {
    config_id: RetrievalConfig(config_id)
    for config_id in (
        "R0_full_hybrid",
        "R1_keyword_only",
        "R2_vector_only",
        "R3_no_graph",
        "R4_no_owner_guard",
    )
}


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """Normalized result accepted from a production or fake retriever."""

    memory_ids: tuple[str, ...] = ()
    latency_ms: float | None = None
    vector_hits: int = 0
    graph_hits: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def memoryIds(self) -> tuple[str, ...]:
        return self.memory_ids


class RetrieverProtocol(Protocol):
    async def search(self, **kwargs: Any) -> Any:
        """Return memory IDs, a mapping, or an object with memory_ids."""


def _field(value: Any, *names: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _normalize_result(value: Any, *, elapsed_ms: float) -> RetrievalResult:
    if isinstance(value, RetrievalResult):
        latency = value.latency_ms if value.latency_ms is not None else elapsed_ms
        return RetrievalResult(
            memory_ids=tuple(str(item) for item in value.memory_ids),
            latency_ms=float(latency),
            vector_hits=int(value.vector_hits),
            graph_hits=int(value.graph_hits),
            metadata=dict(value.metadata),
        )
    if isinstance(value, Mapping):
        raw_ids = _field(value, "memory_ids", "memoryIds", "ids", "results", default=())
    elif isinstance(value, (str, bytes)):
        raw_ids = (value,)
    elif isinstance(value, Sequence):
        raw_ids = value
    else:
        raw_ids = _field(value, "memory_ids", "memoryIds", "ids", "results", default=())
    if raw_ids is None:
        raw_ids = ()
    if isinstance(raw_ids, (str, bytes)):
        raw_ids = (raw_ids,)
    try:
        memory_ids = tuple(str(item) for item in raw_ids)
    except TypeError as exc:
        raise TypeError("retriever result must contain an iterable memory_ids") from exc
    reported_latency = _field(
        value,
        "latency_ms",
        "latencyMs",
        "retrieval_latency_ms",
        "retrievalLatencyMs",
        default=None,
    )
    latency = elapsed_ms if reported_latency is None else float(reported_latency)
    return RetrievalResult(
        memory_ids=memory_ids,
        latency_ms=latency,
        vector_hits=int(_field(value, "vector_hits", "vectorHits", default=0) or 0),
        graph_hits=int(_field(value, "graph_hits", "graphHits", default=0) or 0),
        metadata=dict(value) if isinstance(value, Mapping) else {},
    )


def _parameter_names(callable_value: Callable[..., Any]) -> tuple[set[str], bool]:
    try:
        signature = inspect.signature(callable_value)
    except (TypeError, ValueError):
        return set(), False
    parameters = signature.parameters
    return set(parameters), any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _invoke_retriever(
    retriever: Any,
    case: MemoryQueryCase,
    config: RetrievalConfig,
) -> Any:
    target = getattr(retriever, "search", retriever)
    names, accepts_kwargs = _parameter_names(target)
    kwargs: dict[str, Any] = {}
    if accepts_kwargs or "run_id" in names:
        kwargs["run_id"] = case.run_id
    if accepts_kwargs or "owner_npc_id" in names:
        kwargs["owner_npc_id"] = case.owner_npc_id
    if accepts_kwargs or "query" in names:
        kwargs["query"] = case.query
    for parameter_name in ("retrieval_config", "config", "policy", "ablation"):
        if accepts_kwargs or parameter_name in names:
            kwargs[parameter_name] = config
            break
    if "case" in names and "case" not in kwargs:
        kwargs["case"] = case
    if kwargs:
        return await _maybe_await(target(**kwargs))

    # Signature-less third-party callables are uncommon, but accepting the
    # two conventional forms keeps the runner genuinely injectable.
    try:
        return await _maybe_await(target(case, config))
    except TypeError as first_error:
        try:
            return await _maybe_await(target(case))
        except TypeError:
            raise first_error


def _coerce_config(value: RetrievalConfig | str) -> RetrievalConfig:
    if isinstance(value, RetrievalConfig):
        return value
    if isinstance(value, str):
        return DEFAULT_CONFIGS.get(value, RetrievalConfig(value))
    raise TypeError("configs must contain RetrievalConfig instances or IDs")


def _coerce_case(value: MemoryQueryCase | Mapping[str, Any]) -> MemoryQueryCase:
    if isinstance(value, MemoryQueryCase):
        return value
    case_id = str(_field(value, "case_id", "caseId", "id", default=""))
    subset = str(_field(value, "subset", default=""))
    split = str(_field(value, "split", default=""))
    owner = str(_field(value, "owner_npc_id", "ownerNpcId", "owner", default=""))
    run_id = str(_field(value, "run_id", "runId", default="memory-benchmark-v1"))
    raw_query = _field(value, "query", default={})
    if isinstance(raw_query, MemoryQuerySpec):
        query = raw_query
    elif isinstance(raw_query, Mapping):
        query = MemoryQuerySpec(
            query_text=str(_field(raw_query, "query_text", "queryText", "text", default="")),
            actor_ids=tuple(
                str(item) for item in _field(raw_query, "actor_ids", "actorIds", default=()) or ()
            ),
            goal_ids=tuple(
                str(item) for item in _field(raw_query, "goal_ids", "goalIds", default=()) or ()
            ),
            topic_hints=tuple(
                str(item)
                for item in _field(raw_query, "topic_hints", "topicHints", default=()) or ()
            ),
            graph_seed_memory_ids=tuple(
                str(item)
                for item in _field(
                    raw_query,
                    "graph_seed_memory_ids",
                    "graphSeedMemoryIds",
                    default=(),
                )
                or ()
            ),
            limit=int(_field(raw_query, "limit", "k", default=5)),
        )
    else:
        query = MemoryQuerySpec(query_text=str(raw_query or ""))
    return MemoryQueryCase(
        case_id=case_id,
        subset=subset,
        split=split,
        owner_npc_id=owner,
        run_id=run_id,
        query=query,
        expected_memory_ids=tuple(
            str(item)
            for item in _field(
                value,
                "expected_memory_ids",
                "expectedMemoryIds",
                "expected",
                default=(),
            )
            or ()
        ),
        owner_memory_ids=tuple(
            str(item)
            for item in _field(value, "owner_memory_ids", "ownerMemoryIds", default=()) or ()
        ),
        distractor_memory_ids=tuple(
            str(item)
            for item in _field(
                value,
                "distractor_memory_ids",
                "distractorMemoryIds",
                default=(),
            )
            or ()
        ),
        graph_seed_memory_ids=tuple(
            str(item)
            for item in _field(
                value,
                "graph_seed_memory_ids",
                "graphSeedMemoryIds",
                default=query.graph_seed_memory_ids,
            )
            or ()
        ),
    )


async def _emit_trace(trace_sink: Any, observation: Mapping[str, Any]) -> None:
    if trace_sink is None:
        return
    result = trace_sink(observation)
    if inspect.isawaitable(result):
        await result


async def run_memory_benchmark(
    retriever: RetrieverProtocol | Callable[..., Any],
    dataset: MemoryBenchmarkDataset | Sequence[MemoryQueryCase | Mapping[str, Any]] | str | None = None,
    *,
    cases: Sequence[MemoryQueryCase | Mapping[str, Any]] | None = None,
    configs: Sequence[RetrievalConfig | str] | None = None,
    trace_sink: Callable[[Mapping[str, Any]], Any] | None = None,
    bootstrap_samples: int = 10_000,
    bootstrap_seed: int = 20260901,
) -> dict[str, Any]:
    """Run each case under each named ablation and return report-ready data.

    ``retriever`` is intentionally injected.  It may expose
    ``search(run_id=..., owner_npc_id=..., query=..., retrieval_config=...)``
    or the simpler ``search(case, config)`` shape.  A provider failure is
    retained as an observation and does not silently reduce the denominator.
    """

    if cases is not None:
        selected_cases = tuple(_coerce_case(item) for item in cases)
        dataset_id = "custom"
    elif isinstance(dataset, MemoryBenchmarkDataset):
        selected_cases = dataset.queries
        dataset_id = dataset.dataset_id
    elif dataset is None or isinstance(dataset, (str, bytes)):
        loaded = load_memory_dataset(dataset if isinstance(dataset, str) else None)
        selected_cases = loaded.queries
        dataset_id = loaded.dataset_id
    else:
        selected_cases = tuple(_coerce_case(item) for item in dataset)
        dataset_id = "custom"
    selected_configs = tuple(
        _coerce_config(item) for item in (configs or tuple(DEFAULT_CONFIGS.values()))
    )
    if not selected_cases:
        raise ValueError("memory benchmark requires at least one query case")
    if not selected_configs:
        raise ValueError("memory benchmark requires at least one retrieval config")

    observations: list[dict[str, Any]] = []
    for config in selected_configs:
        for case in selected_cases:
            started = time.perf_counter()
            base: dict[str, Any] = {
                "case_id": case.case_id,
                "caseId": case.case_id,
                "config_id": config.config_id,
                "configId": config.config_id,
                "subset": case.subset,
                "split": case.split,
                "owner_npc_id": case.owner_npc_id,
                "ownerNpcId": case.owner_npc_id,
                "run_id": case.run_id,
                "runId": case.run_id,
                "paired_group": str(case.metadata.get("paired_group", case.run_id)),
                "query_is_empty": case.query_is_empty,
                "queryIsEmpty": case.query_is_empty,
                "expected_memory_ids": list(case.expected_memory_ids),
                "expectedMemoryIds": list(case.expected_memory_ids),
                "retrieval_config": config.as_dict(),
                "error": None,
            }
            try:
                raw_result = await _invoke_retriever(retriever, case, config)
                result = _normalize_result(
                    raw_result,
                    elapsed_ms=(time.perf_counter() - started) * 1000.0,
                )
                metrics = score_retrieval(
                    case.expected_memory_ids,
                    result.memory_ids,
                    case.retrieval_k,
                    owner_memory_ids=case.owner_memory_ids
                    if config.use_owner_guard
                    else (),
                    distractor_memory_ids=case.distractor_memory_ids,
                    query_is_empty=case.query_is_empty,
                )
                base.update(
                    {
                        "retrieved_memory_ids": list(result.memory_ids),
                        "retrievedMemoryIds": list(result.memory_ids),
                        "latency_ms": result.latency_ms,
                        "latencyMs": result.latency_ms,
                        "vector_hits": result.vector_hits,
                        "vectorHits": result.vector_hits,
                        "graph_hits": result.graph_hits,
                        "graphHits": result.graph_hits,
                        "metrics": metrics,
                    }
                )
                # Flattening keeps JSONL useful with command-line tools while
                # retaining the nested metrics contract for API consumers.
                base.update(metrics)
            except Exception as exc:  # noqa: BLE001 - failures are benchmark data
                base.update(
                    {
                        "retrieved_memory_ids": [],
                        "retrievedMemoryIds": [],
                        "latency_ms": (time.perf_counter() - started) * 1000.0,
                        "latencyMs": (time.perf_counter() - started) * 1000.0,
                        "vector_hits": 0,
                        "graph_hits": 0,
                        "metrics": {},
                        "error": type(exc).__name__,
                        "error_message": str(exc),
                    }
                )
            observations.append(base)
            await _emit_trace(trace_sink, base)

    aggregate = aggregate_observations(observations)
    effects = compute_paired_effects(
        observations,
        bootstrap_samples=bootstrap_samples,
        seed=bootstrap_seed,
    )
    return {
        "schema_version": 1,
        "schemaVersion": 1,
        "dataset_id": dataset_id,
        "datasetId": dataset_id,
        "case_count": len(selected_cases),
        "config_count": len(selected_configs),
        "config_ids": [config.config_id for config in selected_configs],
        "configs": [config.as_dict() for config in selected_configs],
        "observations": observations,
        "cases": observations,
        "aggregate": aggregate,
        "paired_effects": effects,
        "pairedEffects": effects,
        "completed": all(item.get("error") is None for item in observations),
    }


benchmark_memory_retriever = run_memory_benchmark
run_memory_retrieval_benchmark = run_memory_benchmark


class StaticRetriever:
    """Tiny deterministic retriever useful for offline smoke tests.

    Keys may be ``(config_id, case_id)``, ``case_id`` or query text.  It is
    intentionally a test helper, not a replacement for production retrieval.
    """

    def __init__(self, responses: Mapping[Any, Iterable[str]], *, default: Iterable[str] = ()) -> None:
        self.responses = {key: tuple(str(item) for item in value) for key, value in responses.items()}
        self.default = tuple(str(item) for item in default)

    async def search(
        self,
        *,
        run_id: str,
        owner_npc_id: str,
        query: MemoryQuerySpec,
        retrieval_config: RetrievalConfig | None = None,
    ) -> RetrievalResult:
        config_id = retrieval_config.config_id if retrieval_config else "R0_full_hybrid"
        key_candidates = (
            (config_id, getattr(query, "case_id", None)),
            getattr(query, "case_id", None),
            query.query_text,
        )
        for key in key_candidates:
            if key in self.responses:
                return RetrievalResult(self.responses[key])
        return RetrievalResult(self.default)


MemoryBenchmarkReport = dict[str, Any]


__all__ = [
    "DEFAULT_CONFIGS",
    "AblationConfig",
    "ConfigId",
    "MemoryBenchmarkReport",
    "RetrievalConfig",
    "RetrievalResult",
    "RetrieverProtocol",
    "StaticRetriever",
    "benchmark_memory_retriever",
    "run_memory_benchmark",
    "run_memory_retrieval_benchmark",
]
