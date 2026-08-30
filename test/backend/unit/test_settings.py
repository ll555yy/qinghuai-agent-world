from __future__ import annotations

from pathlib import Path

import pytest

from core.backend.app.ai.embedding import MEMORY_EMBEDDING_DIMENSIONS
from core.backend.app.settings import Settings


def test_settings_default_to_explicit_in_memory_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "QINGHUAI_PERSISTENCE_BACKEND",
        "DATABASE_URL",
        "DATABASE_ECHO",
        "ARK_EMBEDDING_DIMENSIONS",
        "ARK_EMBEDDING_MODEL",
        "ARK_EMBEDDING_BASE_URL",
        "SEGMENT_SUMMARY_THRESHOLD",
        "SEGMENT_RECENT_MESSAGES",
        "ARK_MODEL_MAX_CONCURRENCY",
        "ARK_DECISION_TEMPERATURE",
        "ARK_SPEECH_TEMPERATURE",
        "ARK_AUXILIARY_TEMPERATURE",
        "CHAT_COOLDOWN_SECONDS",
        "CHAT_PUBLISH_DELAY_MIN_SECONDS",
        "CHAT_PUBLISH_DELAY_MAX_SECONDS",
        "CHAT_MODEL_CALL_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_environment()

    assert settings.persistence_backend == "memory"
    assert settings.database_url is None
    assert settings.scenario_dir == Path(__file__).resolve().parents[3] / "core" / "scenario"
    assert settings.model_max_concurrency == 6
    assert settings.decision_temperature == 0.1
    assert settings.speech_temperature == 0.5
    assert settings.auxiliary_temperature == 0.2
    assert settings.chat_cooldown_seconds == 12.0
    assert settings.chat_publish_delay_min_seconds == 1.2
    assert settings.chat_publish_delay_max_seconds == 3.0
    assert settings.chat_model_call_timeout_seconds == 45.0


def test_postgres_backend_requires_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QINGHUAI_PERSISTENCE_BACKEND", "postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValueError, match="DATABASE_URL"):
        Settings.from_environment()


def test_database_and_summary_settings_are_parsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QINGHUAI_PERSISTENCE_BACKEND", "postgres")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://qinghuai:secret@127.0.0.1:5432/qinghuai",
    )
    monkeypatch.setenv("DATABASE_ECHO", "true")
    monkeypatch.setenv(
        "ARK_EMBEDDING_DIMENSIONS", str(MEMORY_EMBEDDING_DIMENSIONS)
    )
    monkeypatch.setenv("ARK_EMBEDDING_MODEL", "ep-test")
    monkeypatch.setenv("ARK_EMBEDDING_BASE_URL", "https://example.invalid/api/v3")
    monkeypatch.setenv("SEGMENT_SUMMARY_THRESHOLD", "24")
    monkeypatch.setenv("SEGMENT_SUMMARY_TOKEN_THRESHOLD", "2600")
    monkeypatch.setenv("SEGMENT_RECENT_MESSAGES", "10")
    monkeypatch.setenv("SEGMENT_BOUNDARY_CARRYOVER_MESSAGES", "5")
    monkeypatch.setenv("ARK_MODEL_MAX_CONCURRENCY", "9")
    monkeypatch.setenv("ARK_DECISION_TEMPERATURE", "0.05")
    monkeypatch.setenv("ARK_SPEECH_TEMPERATURE", "0.65")
    monkeypatch.setenv("ARK_AUXILIARY_TEMPERATURE", "0.15")
    monkeypatch.setenv("CHAT_COOLDOWN_SECONDS", "15")
    monkeypatch.setenv("CHAT_PUBLISH_DELAY_MIN_SECONDS", "0.8")
    monkeypatch.setenv("CHAT_PUBLISH_DELAY_MAX_SECONDS", "2.4")
    monkeypatch.setenv("CHAT_MODEL_CALL_TIMEOUT_SECONDS", "7.5")

    settings = Settings.from_environment()

    assert settings.persistence_backend == "postgres"
    assert settings.database_echo is True
    assert settings.memory_embedding_dimensions == MEMORY_EMBEDDING_DIMENSIONS
    assert settings.embedding_model == "ep-test"
    assert settings.embedding_base_url == "https://example.invalid/api/v3"
    assert settings.segment_summary_threshold == 24
    assert settings.segment_summary_token_threshold == 2600
    assert settings.segment_recent_messages == 10
    assert settings.segment_boundary_carryover_messages == 5
    assert settings.model_max_concurrency == 9
    assert settings.decision_temperature == 0.05
    assert settings.speech_temperature == 0.65
    assert settings.auxiliary_temperature == 0.15
    assert settings.chat_cooldown_seconds == 15.0
    assert settings.chat_publish_delay_min_seconds == 0.8
    assert settings.chat_publish_delay_max_seconds == 2.4
    assert settings.chat_model_call_timeout_seconds == 7.5


def test_chat_publish_delays_must_be_ordered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAT_PUBLISH_DELAY_MIN_SECONDS", "3")
    monkeypatch.setenv("CHAT_PUBLISH_DELAY_MAX_SECONDS", "1")

    with pytest.raises(ValueError, match="must not be less"):
        Settings.from_environment()


def test_recent_message_window_must_be_smaller_than_summary_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEGMENT_SUMMARY_THRESHOLD", "8")
    monkeypatch.setenv("SEGMENT_RECENT_MESSAGES", "8")

    with pytest.raises(ValueError, match="must be less"):
        Settings.from_environment()


def test_boundary_carryover_must_fit_inside_recent_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEGMENT_RECENT_MESSAGES", "4")
    monkeypatch.setenv("SEGMENT_BOUNDARY_CARRYOVER_MESSAGES", "5")

    with pytest.raises(ValueError, match="must not exceed"):
        Settings.from_environment()


def test_embedding_dimension_must_match_current_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARK_EMBEDDING_DIMENSIONS", "384")

    with pytest.raises(ValueError, match="must be 2048"):
        Settings.from_environment()


@pytest.mark.parametrize("value", ("2.1", "nan", "inf"))
def test_protocol_temperature_must_be_finite_and_at_most_two(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("ARK_SPEECH_TEMPERATURE", value)

    with pytest.raises(
        ValueError, match="ARK_SPEECH_TEMPERATURE must be between 0 and 2"
    ):
        Settings.from_environment()
