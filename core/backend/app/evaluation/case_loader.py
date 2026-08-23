"""Offline, strict loader for versioned semantic-evaluation cases."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

from .models import EvaluationCase

DEFAULT_CASE_PATH = Path(__file__).resolve().parents[3] / "evaluation" / "agent_semantic_cases.yaml"
KNOWN_NPC_IDS = frozenset({"npc_001", "npc_002", "npc_003", "npc_004", "npc_005"})

_ROOT_KEYS = frozenset({"version", "status", "cases"})
_SENSITIVE_KEY_RE = re.compile(
    r"(?:^|[_-])(api[_-]?key|access[_-]?token|token|auth(?:orization)?|password|passwd|"
    r"database[_-]?url|dsn|private[_-]?key|client[_-]?secret|secret[_-]?key|secret)(?:$|[_-])",
    re.IGNORECASE,
)
_SENSITIVE_VALUE_RES = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{12,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{16,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b", re.IGNORECASE),
    re.compile(
        r"\b(?:api[_-]?key|access[_-]?token|password|client[_-]?secret)\s*[:=]\s*[^\s,;]{8,}",
        re.IGNORECASE,
    ),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"(?:postgres(?:ql)?|mysql|redis)://[^\s]+", re.IGNORECASE),
    re.compile(r"-----BEGIN\s+(?:RSA\s+)?PRIVATE KEY-----", re.IGNORECASE),
)


class CaseValidationError(ValueError):
    """A stable, human-readable validation failure from :class:`CaseLoader`."""

    def __init__(self, message: str, *, path: str | None = None) -> None:
        self.path = path
        self.message = f"{path}: {message}" if path else message
        super().__init__(self.message)


def _duplicate(values: Iterable[str]) -> str | None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            return value
        seen.add(value)
    return None


def _scan_input_context(value: Any, path: str) -> None:
    """Reject likely credentials before Pydantic turns YAML into a model."""

    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key)
            if _SENSITIVE_KEY_RE.search(key_text.replace(" ", "_")):
                raise CaseValidationError("credential-like field is not allowed", path=f"{path}.{key_text}")
            _scan_input_context(nested, f"{path}.{key_text}")
        return
    if isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _scan_input_context(nested, f"{path}[{index}]")
        return
    if isinstance(value, str):
        for pattern in _SENSITIVE_VALUE_RES:
            if pattern.search(value):
                raise CaseValidationError("credential-like value is not allowed", path=path)


def _ensure_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CaseValidationError("expected a mapping", path=path)
    return value


class CaseLoader:
    """Load and validate cases without constructing a model or network client."""

    def __init__(
        self,
        source: str | Path | None = None,
        *,
        known_npc_ids: Iterable[str] = KNOWN_NPC_IDS,
    ) -> None:
        self.source = Path(source) if source is not None else DEFAULT_CASE_PATH
        self.known_npc_ids = frozenset(known_npc_ids)

    def load(self, source: str | Path | None = None) -> tuple[EvaluationCase, ...]:
        path = Path(source) if source is not None else self.source
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise CaseValidationError("case file does not exist", path=str(path)) from exc
        except OSError as exc:
            raise CaseValidationError(str(exc), path=str(path)) from exc
        except yaml.YAMLError as exc:
            raise CaseValidationError(f"invalid YAML: {exc}", path=str(path)) from exc
        return self.load_data(raw, source_name=str(path))

    def load_data(
        self,
        raw: Any,
        *,
        source_name: str = "cases",
    ) -> tuple[EvaluationCase, ...]:
        root = _ensure_mapping(raw, source_name)
        unknown_root_keys = sorted(set(root) - _ROOT_KEYS)
        if unknown_root_keys:
            raise CaseValidationError(
                f"unknown root field(s): {', '.join(map(str, unknown_root_keys))}",
                path=source_name,
            )
        version = root.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise CaseValidationError("version must be an integer >= 1", path=f"{source_name}.version")
        raw_cases = root.get("cases")
        if not isinstance(raw_cases, list):
            raise CaseValidationError("cases must be a list", path=f"{source_name}.cases")

        loaded: list[EvaluationCase] = []
        seen_ids: set[str] = set()
        for index, raw_case in enumerate(raw_cases):
            case_path = f"{source_name}.cases[{index}]"
            mapping = _ensure_mapping(raw_case, case_path)
            # Credentials are forbidden anywhere in a Case, not only in its
            # context.  This also covers accidental secrets in rubrics, tags,
            # expected values, or future fields before Pydantic sees them.
            _scan_input_context(mapping, case_path)
            raw_npc_id = mapping.get("npcId", mapping.get("npc_id"))
            if raw_npc_id not in self.known_npc_ids:
                raise CaseValidationError(
                    f"unknown npc_id {raw_npc_id!r}",
                    path=f"{case_path}.npcId",
                )
            try:
                case = EvaluationCase.model_validate(mapping)
            except ValueError as exc:
                raise CaseValidationError(str(exc), path=case_path) from exc
            if case.npc_id not in self.known_npc_ids:
                raise CaseValidationError(
                    f"unknown npc_id {case.npc_id!r}",
                    path=f"{case_path}.npcId",
                )
            if case.case_id in seen_ids:
                raise CaseValidationError("duplicate case_id", path=f"{case_path}.caseId")
            seen_ids.add(case.case_id)
            duplicate_memory = _duplicate(case.expected_memory_ids)
            if duplicate_memory is not None:
                raise CaseValidationError(
                    f"duplicate expected memory id {duplicate_memory!r}",
                    path=f"{case_path}.expectedMemoryIds",
                )
            duplicate_evidence = _duplicate(case.allowed_evidence_message_ids)
            if duplicate_evidence is not None:
                raise CaseValidationError(
                    f"duplicate evidence message id {duplicate_evidence!r}",
                    path=f"{case_path}.allowedEvidenceMessageIds",
                )
            loaded.append(case)

        return tuple(loaded)

    @classmethod
    def load_file(cls, path: str | Path) -> tuple[EvaluationCase, ...]:
        return cls(path).load()


def load_cases(path: str | Path = DEFAULT_CASE_PATH) -> tuple[EvaluationCase, ...]:
    """Convenience function used by small scripts and tests."""

    return CaseLoader(path).load()


__all__ = [
    "CaseLoader",
    "CaseValidationError",
    "DEFAULT_CASE_PATH",
    "KNOWN_NPC_IDS",
    "load_cases",
]
