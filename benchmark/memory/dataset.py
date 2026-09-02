"""Frozen Memory/RAG benchmark dataset and schema validation.

The production memory retriever has a deliberately small interface.  This
module keeps benchmark data independent from that implementation: a case is
just an owner-scoped query plus its expected memory IDs.  The loader performs
all denominator and split checks before a run can start, which prevents an
accidental partial dataset from producing a resume-worthy number.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

import yaml

SubsetName = Literal[
    "exact_keyword",
    "semantic_paraphrase",
    "topic_alias",
    "actor_goal_filter",
    "graph_only",
    "cross_owner_distractor",
    "hard_negative_empty",
]
SplitName = Literal["tuning", "holdout"]


# Keep this mapping public.  It is used by report builders to render every
# subset even when a retriever returned no rows for one of them.
SUBSET_QUOTAS: Mapping[str, Mapping[str, int]] = MappingProxyType(
    {
        "exact_keyword": {"total": 15, "tuning": 4, "holdout": 11},
        "semantic_paraphrase": {"total": 20, "tuning": 6, "holdout": 14},
        "topic_alias": {"total": 15, "tuning": 5, "holdout": 10},
        "actor_goal_filter": {"total": 15, "tuning": 4, "holdout": 11},
        "graph_only": {"total": 15, "tuning": 4, "holdout": 11},
        "cross_owner_distractor": {"total": 10, "tuning": 3, "holdout": 7},
        "hard_negative_empty": {"total": 10, "tuning": 4, "holdout": 6},
    }
)
SUBSET_NAMES = frozenset(SUBSET_QUOTAS)
SPLIT_QUOTAS: Mapping[str, int] = MappingProxyType({"tuning": 30, "holdout": 70})
DATASET_PATH = Path(__file__).with_name("dataset_v1.yaml")


class DatasetValidationError(ValueError):
    """Raised when the frozen benchmark contract is not satisfied."""


def _string_tuple(value: Any, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise DatasetValidationError(f"{field_name} must be a list of strings")
    result = tuple(str(item).strip() for item in value)
    if any(not item for item in result):
        raise DatasetValidationError(f"{field_name} cannot contain empty IDs")
    if len(result) != len(set(result)):
        raise DatasetValidationError(f"{field_name} contains duplicate IDs")
    return result


def _mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise DatasetValidationError(f"{field_name} must be a mapping")
    return value


def _first(mapping: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return default


@dataclass(frozen=True, slots=True)
class MemoryQuerySpec:
    """Provider-neutral query shape passed to an injected retriever."""

    query_text: str = ""
    actor_ids: tuple[str, ...] = ()
    goal_ids: tuple[str, ...] = ()
    topic_hints: tuple[str, ...] = ()
    # Graph-only probes use this benchmark extension.  Production adapters
    # may translate it to their RetrievalPolicy; ordinary MemoryQuery users
    # can simply ignore the optional attribute.
    graph_seed_memory_ids: tuple[str, ...] = ()
    limit: int = 5

    def __post_init__(self) -> None:
        if not isinstance(self.query_text, str):
            raise DatasetValidationError("queryText must be a string")
        if not 1 <= int(self.limit) <= 8:
            raise DatasetValidationError("query limit must be between 1 and 8")
        object.__setattr__(self, "limit", int(self.limit))

    @property
    def queryText(self) -> str:
        return self.query_text

    @property
    def actorIds(self) -> tuple[str, ...]:
        return self.actor_ids

    @property
    def goalIds(self) -> tuple[str, ...]:
        return self.goal_ids

    @property
    def topicHints(self) -> tuple[str, ...]:
        return self.topic_hints

    @property
    def graphSeedMemoryIds(self) -> tuple[str, ...]:
        return self.graph_seed_memory_ids

    @property
    def is_empty(self) -> bool:
        return not (
            self.query_text.strip()
            or self.actor_ids
            or self.goal_ids
            or self.topic_hints
            or self.graph_seed_memory_ids
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "queryText": self.query_text,
            "actorIds": list(self.actor_ids),
            "goalIds": list(self.goal_ids),
            "topicHints": list(self.topic_hints),
            "graphSeedMemoryIds": list(self.graph_seed_memory_ids),
            "limit": self.limit,
        }


@dataclass(frozen=True, slots=True)
class MemoryCorpusDocument:
    """A synthetic corpus row used to document owner and graph labels."""

    memory_id: str
    owner_npc_id: str
    run_id: str
    content: str
    actor_ids: tuple[str, ...] = ()
    goal_ids: tuple[str, ...] = ()
    topic_ids: tuple[str, ...] = ()
    graph_neighbors: tuple[str, ...] = ()

    @property
    def memoryId(self) -> str:
        return self.memory_id

    @property
    def ownerNpcId(self) -> str:
        return self.owner_npc_id


@dataclass(frozen=True, slots=True)
class MemoryQueryCase:
    """One frozen query and its owner-scoped relevance labels."""

    case_id: str
    subset: str
    split: str
    owner_npc_id: str
    run_id: str
    query: MemoryQuerySpec
    expected_memory_ids: tuple[str, ...] = ()
    owner_memory_ids: tuple[str, ...] = ()
    distractor_memory_ids: tuple[str, ...] = ()
    graph_seed_memory_ids: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("case_id", "owner_npc_id", "run_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise DatasetValidationError(f"{field_name} must be non-empty")
        if self.subset not in SUBSET_NAMES:
            raise DatasetValidationError(f"unsupported subset: {self.subset}")
        if self.split not in SPLIT_QUOTAS:
            raise DatasetValidationError(f"unsupported split: {self.split}")
        if not isinstance(self.query, MemoryQuerySpec):
            raise DatasetValidationError("query must be MemoryQuerySpec")
        if len(self.expected_memory_ids) > self.query.limit:
            raise DatasetValidationError(
                f"{self.case_id}: expected labels cannot exceed query limit"
            )

    @property
    def id(self) -> str:
        return self.case_id

    @property
    def query_text(self) -> str:
        return self.query.query_text

    @property
    def actor_ids(self) -> tuple[str, ...]:
        return self.query.actor_ids

    @property
    def goal_ids(self) -> tuple[str, ...]:
        return self.query.goal_ids

    @property
    def topic_hints(self) -> tuple[str, ...]:
        return self.query.topic_hints

    @property
    def retrieval_k(self) -> int:
        return self.query.limit

    @property
    def query_is_empty(self) -> bool:
        return self.query.is_empty

    @property
    def expected_memory_ids_set(self) -> frozenset[str]:
        return frozenset(self.expected_memory_ids)

    # Camel-case aliases are useful to adapters that mirror the backend JSON
    # contract, while the benchmark's Python API remains snake_case.
    @property
    def caseId(self) -> str:
        return self.case_id

    @property
    def ownerNpcId(self) -> str:
        return self.owner_npc_id

    @property
    def expectedMemoryIds(self) -> tuple[str, ...]:
        return self.expected_memory_ids

    @property
    def ownerMemoryIds(self) -> tuple[str, ...]:
        return self.owner_memory_ids

    def as_dict(self) -> dict[str, Any]:
        return {
            "caseId": self.case_id,
            "subset": self.subset,
            "split": self.split,
            "ownerNpcId": self.owner_npc_id,
            "runId": self.run_id,
            "query": self.query.as_dict(),
            "expectedMemoryIds": list(self.expected_memory_ids),
            "ownerMemoryIds": list(self.owner_memory_ids),
            "distractorMemoryIds": list(self.distractor_memory_ids),
            "graphSeedMemoryIds": list(self.graph_seed_memory_ids),
        }


@dataclass(frozen=True, slots=True)
class MemoryBenchmarkDataset:
    dataset_id: str
    schema_version: int
    queries: tuple[MemoryQueryCase, ...]
    corpus: tuple[MemoryCorpusDocument, ...] = ()
    subset_quotas: Mapping[str, Mapping[str, int]] = field(
        default_factory=lambda: SUBSET_QUOTAS
    )
    holdout_labels_frozen: bool = True

    @property
    def cases(self) -> tuple[MemoryQueryCase, ...]:
        return self.queries

    @property
    def query_count(self) -> int:
        return len(self.queries)

    @property
    def corpus_by_id(self) -> Mapping[str, MemoryCorpusDocument]:
        return MappingProxyType({item.memory_id: item for item in self.corpus})

    def by_id(self, case_id: str) -> MemoryQueryCase:
        for case in self.queries:
            if case.case_id == case_id:
                return case
        raise KeyError(case_id)

    def counts(self) -> dict[str, dict[str, int]]:
        result = {
            subset: {"total": 0, "tuning": 0, "holdout": 0}
            for subset in SUBSET_QUOTAS
        }
        for case in self.queries:
            result[case.subset]["total"] += 1
            result[case.subset][case.split] += 1
        return result

    def validate(self, *, require_frozen: bool = True) -> MemoryBenchmarkDataset:
        _validate_dataset(self, require_frozen=require_frozen)
        return self


def _parse_query(raw: Any, *, case_id: str) -> MemoryQuerySpec:
    if isinstance(raw, str):
        raw = {"queryText": raw}
    query = _mapping(raw, field_name=f"{case_id}.query")
    text = _first(query, "queryText", "query_text", "text", default="")
    actor_ids = _string_tuple(
        _first(query, "actorIds", "actor_ids", default=()),
        field_name=f"{case_id}.query.actorIds",
    )
    goal_ids = _string_tuple(
        _first(query, "goalIds", "goal_ids", default=()),
        field_name=f"{case_id}.query.goalIds",
    )
    topic_hints = _string_tuple(
        _first(query, "topicHints", "topic_hints", default=()),
        field_name=f"{case_id}.query.topicHints",
    )
    graph_seed_memory_ids = _string_tuple(
        _first(query, "graphSeedMemoryIds", "graph_seed_memory_ids", default=()),
        field_name=f"{case_id}.query.graphSeedMemoryIds",
    )
    limit = _first(query, "limit", "k", default=5)
    try:
        limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise DatasetValidationError(f"{case_id}.query.limit must be an integer") from exc
    return MemoryQuerySpec(
        query_text=str(text or ""),
        actor_ids=actor_ids,
        goal_ids=goal_ids,
        topic_hints=topic_hints,
        graph_seed_memory_ids=graph_seed_memory_ids,
        limit=limit,
    )


def _parse_case(raw: Any, index: int) -> MemoryQueryCase:
    item = _mapping(raw, field_name=f"queries[{index}]")
    case_id = str(_first(item, "id", "caseId", "case_id", default="")).strip()
    if not case_id:
        raise DatasetValidationError(f"queries[{index}] is missing id")
    subset = str(_first(item, "subset", "category", default="")).strip()
    split = str(_first(item, "split", default="")).strip()
    owner = str(
        _first(item, "ownerNpcId", "owner_npc_id", "owner", default="")
    ).strip()
    run_id = str(_first(item, "runId", "run_id", default="memory-benchmark-v1")).strip()
    raw_query = _first(item, "query", default={})
    query = _parse_query(raw_query, case_id=case_id)
    expected = _string_tuple(
        _first(item, "expectedMemoryIds", "expected_memory_ids", "expected", default=()),
        field_name=f"{case_id}.expectedMemoryIds",
    )
    owner_memory = _string_tuple(
        _first(item, "ownerMemoryIds", "owner_memory_ids", default=()),
        field_name=f"{case_id}.ownerMemoryIds",
    )
    distractors = _string_tuple(
        _first(item, "distractorMemoryIds", "distractor_memory_ids", default=()),
        field_name=f"{case_id}.distractorMemoryIds",
    )
    query_mapping = _mapping(raw_query, field_name=f"{case_id}.query")
    graph_seeds = _string_tuple(
        _first(
            item,
            "graphSeedMemoryIds",
            "graph_seed_memory_ids",
            default=query.graph_seed_memory_ids
            or _first(query_mapping, "graphSeedMemoryIds", "graph_seed_memory_ids", default=()),
        ),
        field_name=f"{case_id}.graphSeedMemoryIds",
    )
    known_fields = {
        "id",
        "caseId",
        "case_id",
        "subset",
        "category",
        "split",
        "ownerNpcId",
        "owner_npc_id",
        "owner",
        "runId",
        "run_id",
        "query",
        "expectedMemoryIds",
        "expected_memory_ids",
        "expected",
        "ownerMemoryIds",
        "owner_memory_ids",
        "distractorMemoryIds",
        "distractor_memory_ids",
        "graphSeedMemoryIds",
        "graph_seed_memory_ids",
    }
    metadata = {str(key): value for key, value in item.items() if key not in known_fields}
    return MemoryQueryCase(
        case_id=case_id,
        subset=subset,
        split=split,
        owner_npc_id=owner,
        run_id=run_id,
        query=query,
        expected_memory_ids=expected,
        owner_memory_ids=owner_memory,
        distractor_memory_ids=distractors,
        graph_seed_memory_ids=graph_seeds,
        metadata=MappingProxyType(metadata),
    )


def _parse_corpus(raw: Any) -> tuple[MemoryCorpusDocument, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise DatasetValidationError("corpus must be a list")
    result: list[MemoryCorpusDocument] = []
    for index, value in enumerate(raw):
        item = _mapping(value, field_name=f"corpus[{index}]")
        memory_id = str(_first(item, "memoryId", "memory_id", "id", default="")).strip()
        owner = str(_first(item, "ownerNpcId", "owner_npc_id", "owner", default="")).strip()
        run_id = str(_first(item, "runId", "run_id", default="memory-benchmark-v1")).strip()
        content = str(_first(item, "content", "text", default=""))
        if not memory_id or not owner or not run_id or not content:
            raise DatasetValidationError(
                f"corpus[{index}] requires memory_id, owner_npc_id, run_id and content"
            )
        result.append(
            MemoryCorpusDocument(
                memory_id=memory_id,
                owner_npc_id=owner,
                run_id=run_id,
                content=content,
                actor_ids=_string_tuple(
                    _first(item, "actorIds", "actor_ids", default=()),
                    field_name=f"corpus[{index}].actorIds",
                ),
                goal_ids=_string_tuple(
                    _first(item, "goalIds", "goal_ids", default=()),
                    field_name=f"corpus[{index}].goalIds",
                ),
                topic_ids=_string_tuple(
                    _first(item, "topicIds", "topic_ids", default=()),
                    field_name=f"corpus[{index}].topicIds",
                ),
                graph_neighbors=_string_tuple(
                    _first(item, "graphNeighbors", "graph_neighbors", default=()),
                    field_name=f"corpus[{index}].graphNeighbors",
                ),
            )
        )
    return tuple(result)


def _validate_dataset(dataset: MemoryBenchmarkDataset, *, require_frozen: bool) -> None:
    if dataset.schema_version != 1:
        raise DatasetValidationError("only schemaVersion 1 is supported")
    if not dataset.dataset_id.strip():
        raise DatasetValidationError("datasetId must be non-empty")
    if require_frozen and not dataset.holdout_labels_frozen:
        raise DatasetValidationError("holdout labels must be frozen before a run")
    if len(dataset.queries) != 100:
        raise DatasetValidationError(
            f"dataset must contain exactly 100 queries, got {len(dataset.queries)}"
        )
    case_ids = [case.case_id for case in dataset.queries]
    if len(case_ids) != len(set(case_ids)):
        raise DatasetValidationError("query IDs must be unique")
    counts = dataset.counts()
    for subset, quota in SUBSET_QUOTAS.items():
        if counts.get(subset) != dict(quota):
            raise DatasetValidationError(
                f"{subset} quota mismatch: expected {dict(quota)}, got {counts.get(subset)}"
            )
    split_counts = {
        split: sum(1 for case in dataset.queries if case.split == split)
        for split in SPLIT_QUOTAS
    }
    if split_counts != dict(SPLIT_QUOTAS):
        raise DatasetValidationError(
            f"split counts must be {dict(SPLIT_QUOTAS)}, got {split_counts}"
        )

    corpus_by_id = {item.memory_id: item for item in dataset.corpus}
    if len(corpus_by_id) != len(dataset.corpus):
        raise DatasetValidationError("corpus memory IDs must be unique")
    for document in dataset.corpus:
        if document.run_id != "memory-benchmark-v1":
            raise DatasetValidationError(
                f"{document.memory_id}: corpus run_id must be memory-benchmark-v1"
            )
        for neighbor in document.graph_neighbors:
            if neighbor not in corpus_by_id:
                raise DatasetValidationError(
                    f"{document.memory_id}: unknown graph neighbor {neighbor}"
                )
            if corpus_by_id[neighbor].owner_npc_id != document.owner_npc_id:
                raise DatasetValidationError(
                    f"{document.memory_id}: graph neighbor crosses owner boundary"
                )

    for case in dataset.queries:
        expected = set(case.expected_memory_ids)
        owner_memory = set(case.owner_memory_ids)
        distractors = set(case.distractor_memory_ids)
        graph_seeds = set(case.graph_seed_memory_ids)
        if not expected.issubset(owner_memory):
            raise DatasetValidationError(
                f"{case.case_id}: expected memory must be in ownerMemoryIds"
            )
        if expected & distractors:
            raise DatasetValidationError(
                f"{case.case_id}: expected and distractor memories overlap"
            )
        if owner_memory & distractors:
            raise DatasetValidationError(
                f"{case.case_id}: owner and distractor memories overlap"
            )
        if not graph_seeds.issubset(owner_memory):
            raise DatasetValidationError(
                f"{case.case_id}: graph seeds must be owner-scoped"
            )
        if case.query_is_empty and case.subset != "hard_negative_empty":
            raise DatasetValidationError(
                f"{case.case_id}: empty query is only allowed in hard_negative_empty"
            )
        if case.subset == "hard_negative_empty" and not expected:
            # Empty queries and hard negatives are both valid in this subset.
            pass
        if corpus_by_id:
            for memory_id in expected | owner_memory | graph_seeds:
                document = corpus_by_id.get(memory_id)
                if document is None:
                    raise DatasetValidationError(
                        f"{case.case_id}: unknown corpus memory {memory_id}"
                    )
                if document.owner_npc_id != case.owner_npc_id:
                    raise DatasetValidationError(
                        f"{case.case_id}: corpus memory {memory_id} has wrong owner"
                    )
            for memory_id in distractors:
                document = corpus_by_id.get(memory_id)
                if document is not None and document.owner_npc_id == case.owner_npc_id:
                    raise DatasetValidationError(
                        f"{case.case_id}: distractor {memory_id} has query owner"
                    )


def _dataset_from_raw(raw: Mapping[str, Any]) -> MemoryBenchmarkDataset:
    schema_version = _first(raw, "schemaVersion", "schema_version", default=1)
    try:
        schema_version = int(schema_version)
    except (TypeError, ValueError) as exc:
        raise DatasetValidationError("schemaVersion must be an integer") from exc
    dataset_id = str(_first(raw, "datasetId", "dataset_id", default="")).strip()
    raw_queries = _first(raw, "queries", "cases", default=None)
    if not isinstance(raw_queries, Sequence) or isinstance(raw_queries, str):
        raise DatasetValidationError("queries must be a list")
    queries = tuple(_parse_case(item, index) for index, item in enumerate(raw_queries))
    corpus = _parse_corpus(_first(raw, "corpus", "memories", default=()))
    frozen = bool(
        _first(raw, "holdoutLabelsFrozen", "holdout_labels_frozen", default=True)
    )
    return MemoryBenchmarkDataset(
        dataset_id=dataset_id,
        schema_version=schema_version,
        queries=queries,
        corpus=corpus,
        subset_quotas=SUBSET_QUOTAS,
        holdout_labels_frozen=frozen,
    )


def _read_source(source: str | Path | Mapping[str, Any] | None) -> Mapping[str, Any]:
    if source is None:
        path = DATASET_PATH
        if not path.exists():
            raise DatasetValidationError(f"dataset file does not exist: {path}")
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    elif isinstance(source, Mapping):
        raw = source
    else:
        path = Path(source)
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    if not isinstance(raw, Mapping):
        raise DatasetValidationError("dataset root must be a mapping")
    return raw


def load_memory_dataset(
    source: str | Path | Mapping[str, Any] | None = None,
    *,
    require_frozen: bool = True,
) -> MemoryBenchmarkDataset:
    """Load and validate the frozen 100-query dataset."""

    dataset = _dataset_from_raw(_read_source(source))
    return dataset.validate(require_frozen=require_frozen)


def load_dataset(
    source: str | Path | Mapping[str, Any] | None = None,
    *,
    require_frozen: bool = True,
) -> MemoryBenchmarkDataset:
    """Short alias used by the benchmark CLI and external callers."""

    return load_memory_dataset(source, require_frozen=require_frozen)


def validate_dataset(
    source: str | Path | Mapping[str, Any] | MemoryBenchmarkDataset | None = None,
    *,
    require_frozen: bool = True,
) -> MemoryBenchmarkDataset:
    """Validate a path, raw mapping or already-loaded dataset."""

    if isinstance(source, MemoryBenchmarkDataset):
        return source.validate(require_frozen=require_frozen)
    return load_memory_dataset(source, require_frozen=require_frozen)


__all__ = [
    "DATASET_PATH",
    "SPLIT_QUOTAS",
    "SUBSET_NAMES",
    "SUBSET_QUOTAS",
    "DatasetValidationError",
    "MemoryBenchmarkDataset",
    "MemoryCorpusDocument",
    "MemoryQueryCase",
    "MemoryQuerySpec",
    "load_dataset",
    "load_memory_dataset",
    "validate_dataset",
]
