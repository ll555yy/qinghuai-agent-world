"""Memory/RAG benchmark package.

The benchmark deliberately lives outside the production retriever.  It owns
the frozen dataset, ablation configuration, result scoring and an injectable
runner so that a benchmark run can exercise either the PostgreSQL retriever
or a small test double without changing production behaviour.
"""

from .dataset import (
    DATASET_PATH,
    SUBSET_QUOTAS,
    DatasetValidationError,
    MemoryBenchmarkDataset,
    MemoryCorpusDocument,
    MemoryQueryCase,
    load_dataset,
    load_memory_dataset,
    validate_dataset,
)
from .runner import (
    DEFAULT_CONFIGS,
    AblationConfig,
    MemoryBenchmarkReport,
    RetrievalConfig,
    RetrievalResult,
    benchmark_memory_retriever,
    run_memory_benchmark,
)
from .scorer import (
    aggregate_observations,
    compute_paired_effects,
    paired_effect,
    score_retrieval,
)

__all__ = [
    "DATASET_PATH",
    "DEFAULT_CONFIGS",
    "SUBSET_QUOTAS",
    "AblationConfig",
    "DatasetValidationError",
    "MemoryBenchmarkDataset",
    "MemoryBenchmarkReport",
    "MemoryCorpusDocument",
    "MemoryQueryCase",
    "RetrievalConfig",
    "RetrievalResult",
    "aggregate_observations",
    "benchmark_memory_retriever",
    "compute_paired_effects",
    "load_dataset",
    "load_memory_dataset",
    "paired_effect",
    "run_memory_benchmark",
    "score_retrieval",
    "validate_dataset",
]
