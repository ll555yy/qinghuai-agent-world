from __future__ import annotations

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """The production backend and psycopg integration use asyncio on Windows."""

    return "asyncio"
