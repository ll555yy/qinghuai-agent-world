from __future__ import annotations

import pytest

from benchmark.memory.dataset import (
    SUBSET_QUOTAS,
    DatasetValidationError,
    load_dataset,
    validate_dataset,
)


def test_frozen_dataset_has_exact_denominator_and_30_70_split() -> None:
    dataset = load_dataset()

    assert dataset.query_count == 100
    assert dataset.counts() == {name: dict(quota) for name, quota in SUBSET_QUOTAS.items()}
    assert sum(item.split == "tuning" for item in dataset.queries) == 30
    assert sum(item.split == "holdout" for item in dataset.queries) == 70
    assert dataset.holdout_labels_frozen is True
    assert len({item.case_id for item in dataset.queries}) == 100


def test_loader_exposes_owner_scoped_query_and_graph_probe() -> None:
    dataset = load_dataset()
    graph_case = dataset.by_id("q066")
    empty_case = dataset.by_id("q096")

    assert graph_case.subset == "graph_only"
    assert graph_case.graph_seed_memory_ids == ("m_lan_site",)
    assert graph_case.query.is_empty is False
    assert empty_case.query_is_empty is True
    assert empty_case.expected_memory_ids == ()


def test_validate_dataset_rejects_missing_query_without_changing_default() -> None:
    dataset = load_dataset()
    raw = {
        "schemaVersion": dataset.schema_version,
        "datasetId": dataset.dataset_id,
        "holdoutLabelsFrozen": True,
        "queries": [item.as_dict() for item in dataset.queries[:-1]],
    }
    with pytest.raises(DatasetValidationError, match="exactly 100"):
        validate_dataset(raw)
