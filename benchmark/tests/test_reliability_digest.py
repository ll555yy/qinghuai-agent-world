from __future__ import annotations

from dataclasses import dataclass, field

from benchmark.reliability import (
    compare_state,
    normalize_state,
    state_digest,
    states_match,
)


def test_digest_ignores_run_identity_timestamps_and_runtime_locks() -> None:
    first = {
        "runId": "run-a",
        "createdAt": "2026-01-01T00:00:00Z",
        "lock": object(),
        "eventSeq": 4,
        "worldTime": {"day": 3, "hour": 18, "minute": 0},
        "goals": {"goal": {"status": "pending"}},
    }
    second = {
        "runId": "run-b",
        "createdAt": "2026-01-02T00:00:00Z",
        "lock": object(),
        "eventSeq": 4,
        "worldTime": {"day": 3, "hour": 18, "minute": 0},
        "goals": {"goal": {"status": "pending"}},
    }
    assert states_match(first, second)
    assert state_digest(first) == state_digest(second)
    assert "runId" not in normalize_state(first)


def test_digest_keeps_domain_state_and_compare_reports_divergence() -> None:
    first = {"eventSeq": 4, "worldTime": {"day": 3, "hour": 18, "minute": 0}, "goals": {"goal": "pending"}}
    second = {"eventSeq": 5, "worldTime": {"day": 3, "hour": 18, "minute": 0}, "goals": {"goal": "pending"}}
    comparison = compare_state(first, second)
    assert comparison.diverged is True
    assert comparison.matches is False


@dataclass
class StateWithRuntime:
    run_id: str
    event_seq: int
    lock: object = field(default_factory=object)


def test_digest_accepts_dataclass_state_without_copying_runtime_handle() -> None:
    assert state_digest(StateWithRuntime("a", 1)) == state_digest(StateWithRuntime("b", 1))
