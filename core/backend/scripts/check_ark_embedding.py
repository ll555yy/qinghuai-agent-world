"""Dry-run by default; probe Ark Embeddings with two fixed public texts."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_ROOT / ".env", override=False)
sys.path.insert(0, str(_ROOT))

from core.backend.app.ai.ark_embedding import (  # noqa: E402
    ArkEmbeddingClient,
    ArkEmbeddingSettings,
)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

_FIXED_TEXTS = (
    "如何保留一家旧书店承载的老街记忆",
    "邻里共同讨论一个空间的公益与经营方案",
)


async def run_probe(*, live: bool) -> tuple[dict[str, object], int]:
    if not live:
        return {"live": False, "requestSent": False, "status": "dry_run"}, 0
    model = os.environ.get("ARK_EMBEDDING_MODEL", "").strip()
    key_present = bool(os.environ.get("ARK_API_KEY", "").strip())
    if not model or not key_present:
        return {
            "live": True,
            "requestSent": False,
            "status": "not_configured",
        }, 2
    settings = ArkEmbeddingSettings(model=model)
    client = ArkEmbeddingClient(settings)
    started = time.perf_counter()
    error_code = None
    success = False
    try:
        vectors = await client.embed_many(_FIXED_TEXTS)
        success = len(vectors) == len(_FIXED_TEXTS)
    except ValueError:
        error_code = "embedding_dimension_mismatch"
    except Exception:
        error_code = "embedding_provider_error"
    finally:
        await client.close()
    metadata = client.last_metadata
    return {
        "live": True,
        "requestSent": True,
        "status": "completed" if success else "failed",
        "success": success,
        "configuredModel": model,
        "baseUrlHost": urlparse(settings.base_url).hostname,
        "expectedDimensions": client.dimensions,
        "actualDimensions": metadata.get("actualDimensions"),
        "actualModel": metadata.get("actualModel"),
        "providerRequestId": metadata.get("providerRequestId"),
        "vectorCount": metadata.get("vectorCount"),
        "totalTokens": metadata.get("totalTokens"),
        "latencyMs": int((time.perf_counter() - started) * 1000),
        "errorCode": error_code,
    }, 0 if success else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    report, code = asyncio.run(run_probe(live=args.live))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
