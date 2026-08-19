"""Runtime settings for the local backend and its persistence adapter."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

from .ai.embedding import MEMORY_EMBEDDING_DIMENSIONS

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_REPOSITORY_ROOT / ".env", override=False)

PersistenceBackend = Literal["memory", "postgres"]


def _boolean(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    scenario_dir: Path
    app_name: str = "Qinghuai Chat Backend"
    persistence_backend: PersistenceBackend = "memory"
    database_url: str | None = None
    database_echo: bool = False
    memory_embedding_dimensions: int = MEMORY_EMBEDDING_DIMENSIONS
    embedding_model: str | None = None
    embedding_base_url: str | None = None
    segment_summary_threshold: int = 20
    segment_recent_messages: int = 8

    @classmethod
    def from_environment(cls) -> Settings:
        default_dir = Path(__file__).resolve().parents[2] / "scenario"
        configured = os.environ.get("QINGHUAI_SCENARIO_DIR")
        raw_backend = os.environ.get("QINGHUAI_PERSISTENCE_BACKEND", "memory").strip().lower()
        if raw_backend not in {"memory", "postgres"}:
            raise ValueError(
                "QINGHUAI_PERSISTENCE_BACKEND must be 'memory' or 'postgres'"
            )
        backend: PersistenceBackend = (
            "postgres" if raw_backend == "postgres" else "memory"
        )
        database_url = os.environ.get("DATABASE_URL")
        if backend == "postgres" and not database_url:
            raise ValueError("DATABASE_URL is required when PostgreSQL persistence is enabled")
        summary_threshold = _positive_int("SEGMENT_SUMMARY_THRESHOLD", 20)
        recent_messages = _positive_int("SEGMENT_RECENT_MESSAGES", 8)
        if recent_messages >= summary_threshold:
            raise ValueError(
                "SEGMENT_RECENT_MESSAGES must be less than SEGMENT_SUMMARY_THRESHOLD"
            )
        embedding_dimensions = _positive_int(
            "ARK_EMBEDDING_DIMENSIONS", MEMORY_EMBEDDING_DIMENSIONS
        )
        if embedding_dimensions != MEMORY_EMBEDDING_DIMENSIONS:
            raise ValueError(
                "ARK_EMBEDDING_DIMENSIONS must be 1024 until an explicit migration is applied"
            )
        embedding_model = os.environ.get("ARK_EMBEDDING_MODEL", "").strip() or None
        embedding_base_url = os.environ.get("ARK_EMBEDDING_BASE_URL", "").strip() or None
        return cls(
            scenario_dir=Path(configured) if configured else default_dir,
            persistence_backend=backend,
            database_url=database_url,
            database_echo=_boolean("DATABASE_ECHO"),
            memory_embedding_dimensions=embedding_dimensions,
            embedding_model=embedding_model,
            embedding_base_url=embedding_base_url,
            segment_summary_threshold=summary_threshold,
            segment_recent_messages=recent_messages,
        )
