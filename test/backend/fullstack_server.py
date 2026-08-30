"""Windows-compatible launcher for the deterministic full-stack test app."""

from __future__ import annotations

import asyncio
import importlib
import selectors
import sys
from pathlib import Path

import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

app = importlib.import_module("fullstack_app").app


def main() -> None:
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=8000,
            loop="none",
        )
    )
    with asyncio.Runner(
        loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
    ) as runner:
        runner.run(server.serve())


if __name__ == "__main__":
    main()
