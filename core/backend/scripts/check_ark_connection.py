"""One-shot local Ark connectivity check.

Run this manually only after setting ``ARK_API_KEY`` in the local environment.
It sends one fixed, non-game prompt and never prints the key or prompt context.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from core.backend.app.ai.ark_client import ArkClient  # noqa: E402
from core.backend.app.ai.errors import AIError  # noqa: E402
from core.backend.app.ai.models import ChatMessage, TextGenerationRequest  # noqa: E402


async def main() -> int:
    if not os.environ.get("ARK_API_KEY", "").strip():
        print("ARK_API_KEY is not configured; no request was sent.")
        return 2
    client = ArkClient()
    try:
        result = await client.generate(
            TextGenerationRequest(
                system_prompt="Reply with exactly OK.",
                messages=[ChatMessage(role="user", content="Connectivity check.")],
                temperature=0,
                max_output_tokens=8,
                request_id="manual-ark-connectivity-check",
            )
        )
    except AIError as exc:
        print(f"Ark connectivity failed: {exc.code}")
        return 1
    finally:
        await client.close()
    print(f"Ark connectivity succeeded: provider={result.provider} model={result.model}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
