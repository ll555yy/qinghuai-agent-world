"""Stable state projection/digest for paired recovery comparisons.

Only process/run identity, timestamps, and runtime synchronization handles are
removed.  Domain state such as event sequence, world time, goals,
relationships, owner-scoped memories, messages, and chapter state remains in
the digest and therefore participates in the divergence check.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from typing import Any

_RUN_ID_KEYS = frozenset({"runid", "run_id", "traceid", "trace_id", "requestid", "request_id"})
_RUNTIME_KEYS = frozenset(
    {
        "lock",
        "task",
        "runtime_lock",
        "runtime_task",
        "runtimelock",
        "runtimetask",
        "conversationroundlock",
        "conversationroundtask",
        "conversation_round_lock",
        "conversation_round_task",
        "asyncio_lock",
        "asyncio_task",
    }
)
_TIMESTAMP_KEYS = frozenset(
    {
        "timestamp",
        "createdat",
        "updatedat",
        "startedat",
        "completedat",
        "endedat",
        "requestedat",
        "receivedat",
        "committedat",
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
        "ended_at",
        "requested_at",
        "received_at",
        "committed_at",
        "occurredat",
        "occurred_at",
    }
)
_UNORDERED_SEQUENCE_KEYS = frozenset(
    {
        "relationships",
        "chapteragendastances",
        "chapter_agenda_stances",
        "consolidationstatus",
        "consolidation_status",
        "memorycache",
        "memory_cache",
    }
)
_SPARSE_EMPTY_MAP_KEYS = frozenset(
    {"thoughDays", "thoughtdays", "thought_days", "freshEventContext", "fresheventcontext", "fresh_event_context"}
)


def _key(value: Any) -> str:
    return str(value)


def _lower_key(value: Any) -> str:
    return _key(value).replace("-", "_").lower()


def _is_run_id_key(value: Any) -> bool:
    return _lower_key(value) in _RUN_ID_KEYS


def _is_runtime_key(value: Any) -> bool:
    normalized = _lower_key(value)
    return normalized in _RUNTIME_KEYS or normalized.endswith(("_lock", "_task"))


def _is_timestamp_key(value: Any) -> bool:
    normalized = _lower_key(value)
    return normalized in _TIMESTAMP_KEYS or normalized.endswith(("_timestamp", "timestamp"))


def _is_runtime_value(value: Any) -> bool:
    module_name = type(value).__module__.lower()
    type_name = type(value).__name__.lower()
    return (
        "asyncio" in module_name
        or "threading" in module_name
        or type_name in {"lock", "rlock", "event", "condition", "task"}
    )


def _object_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: getattr(value, field.name) for field in dataclasses.fields(value)}
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            result = to_dict()
        except TypeError:
            result = None
        if isinstance(result, Mapping):
            return result
    if hasattr(value, "__dict__"):
        raw = vars(value)
        return raw if isinstance(raw, Mapping) else None
    slots = getattr(type(value), "__slots__", ())
    if isinstance(slots, str):
        slots = (slots,)
    if slots:
        return {slot: getattr(value, slot) for slot in slots if hasattr(value, slot)}
    return None


def normalize_state(value: Any, *, _parent_key: str | None = None) -> Any:
    """Return a JSON-shaped canonical state with volatile fields removed."""

    if _is_runtime_value(value):
        return None
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (datetime, date, time)):
        return None
    mapping = _object_mapping(value)
    if mapping is not None:
        result: dict[str, Any] = {}
        for raw_key, raw_value in mapping.items():
            key = _key(raw_key)
            lowered = _lower_key(raw_key)
            if _is_run_id_key(raw_key) or _is_runtime_key(raw_key):
                continue
            # ``worldTime``/``clock`` are domain state and must be compared;
            # their day/hour/minute children are retained below.  Generic
            # timestamps are deliberately ignored.
            if _is_timestamp_key(raw_key):
                continue
            if (
                _parent_key is not None
                and _lower_key(_parent_key) in {_lower_key(item) for item in _SPARSE_EMPTY_MAP_KEYS}
                and raw_value in ({}, [], (), set(), frozenset())
            ):
                continue
            if lowered in {"time", "at"} and not isinstance(raw_value, Mapping):
                continue
            normalized = normalize_state(raw_value, _parent_key=key)
            if normalized is None and raw_value is not None and _is_runtime_value(raw_value):
                continue
            result[key] = normalized
        return {key: result[key] for key in sorted(result)}
    if isinstance(value, (set, frozenset)):
        normalized = [normalize_state(item, _parent_key=_parent_key) for item in value]
        return sorted(normalized, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, default=str))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        normalized = [normalize_state(item, _parent_key=_parent_key) for item in value]
        if _parent_key is not None and _lower_key(_parent_key) in _UNORDERED_SEQUENCE_KEYS:
            return sorted(
                normalized,
                key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, default=str),
            )
        return normalized
    # A domain object without a serializable shape should not make a digest
    # nondeterministic through its memory address.
    return str(value)


def state_digest(value: Any) -> str:
    normalized = normalize_state(value)
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


normalized_state_digest = state_digest
digest_state = state_digest


@dataclasses.dataclass(frozen=True, slots=True)
class StateComparison:
    expected_digest: str
    actual_digest: str
    diverged: bool

    @property
    def matches(self) -> bool:
        return not self.diverged

    def to_dict(self) -> dict[str, Any]:
        return {
            "expectedDigest": self.expected_digest,
            "actualDigest": self.actual_digest,
            "diverged": self.diverged,
            "matches": self.matches,
        }


def compare_state(expected: Any, actual: Any) -> StateComparison:
    expected_digest = state_digest(expected)
    actual_digest = state_digest(actual)
    return StateComparison(expected_digest, actual_digest, expected_digest != actual_digest)


def states_match(expected: Any, actual: Any) -> bool:
    return not compare_state(expected, actual).diverged


__all__ = [
    "StateComparison",
    "compare_state",
    "digest_state",
    "normalize_state",
    "normalized_state_digest",
    "state_digest",
    "states_match",
]
