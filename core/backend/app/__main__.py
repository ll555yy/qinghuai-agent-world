"""Portable development entry point for the FastAPI service."""

from __future__ import annotations

import asyncio
import selectors
import sys

import uvicorn


def _selector_loop() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


def main() -> None:
    server = uvicorn.Server(
        uvicorn.Config(
            "core.backend.app.main:app",
            host="127.0.0.1",
            port=8000,
            loop="none",
        )
    )
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=_selector_loop) as runner:
            runner.run(server.serve())
    else:
        asyncio.run(server.serve())


if __name__ == "__main__":
    main()
