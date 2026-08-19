from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.backend.app.main import app
from core.backend.app.scenario.loader import ScenarioLoader
from core.backend.app.scenario.models import ScenarioRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_DIR = PROJECT_ROOT / "core" / "scenario"


@pytest.fixture()
def registry() -> ScenarioRegistry:
    return ScenarioLoader(SCENARIO_DIR).load()


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"
