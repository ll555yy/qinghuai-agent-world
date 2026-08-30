"""Bounded Ark Responses compatibility check for the preregistered Judge v2.

The primary model receives exactly one physical request.  The fallback model
is tried exactly once only when the primary returns an explicit request/schema
incompatibility or violates the provider-enforced strict response schema.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_ROOT / ".env", override=False)
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.backend.app.ai.ark_client import (  # noqa: E402
    DEFAULT_ARK_BASE_URL,
    ArkSettings,
)
from core.backend.app.ai.errors import AIError, AIErrorCode  # noqa: E402
from core.backend.app.ai.models import (  # noqa: E402
    ChatMessage,
    TextGenerationRequest,
)
from core.backend.app.evaluation.ark_responses import ArkResponsesClient  # noqa: E402
from core.backend.app.evaluation.judge import (  # noqa: E402
    RUBRIC_VERSION,
    build_judge_prompt,
    judge_prompt_sha256,
    judge_schema_sha256,
    parse_judge_score,
)
from core.backend.app.evaluation.judge_protocols import JudgeScore  # noqa: E402

PRIMARY_MODEL = "deepseek-v4-pro"
FALLBACK_MODEL = "deepseek-v4-flash"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation_reports/judge_v2_compatibility.json"),
    )
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    return parser


def _probe_payload() -> tuple[dict[str, Any], dict[str, Any]]:
    case = {
        "case_id": "judge-v2-compatibility-public-001",
        "category": "relevance",
        "protocol": "chat_decision",
        "input_context": {"direct_question": "你支持这个公开测试方案吗？"},
        "expected_constraints": ["直接回答公开测试问题"],
        "forbidden_signals": [],
        "allowed_outcomes": ["support", "conditional", "oppose"],
        "judge_rubric": ["事实和边界优先"],
        "tags": ["synthetic", "public", "compatibility"],
    }
    candidate = {
        "protocol": "chat_decision",
        "candidate_text": "支持，但应先核对公开测试数据。",
    }
    return case, candidate


def _usage_dict(value: object) -> dict[str, int | None]:
    if value is None:
        return {"promptTokens": None, "completionTokens": None, "totalTokens": None}
    return {
        "promptTokens": getattr(value, "prompt_tokens", None),
        "completionTokens": getattr(value, "completion_tokens", None),
        "totalTokens": getattr(value, "total_tokens", None),
    }


async def _probe(
    *,
    model: str,
    api_key: str,
    base_url: str,
    timeout_seconds: float,
) -> tuple[dict[str, Any], bool]:
    settings = ArkSettings(
        api_key=api_key,
        model=model,
        base_url=base_url,
        request_timeout_seconds=timeout_seconds,
    )
    client = ArkResponsesClient(
        settings,
        response_schema=JudgeScore.model_json_schema(),
        max_provider_retries=0,
    )
    case, candidate = _probe_payload()
    system_prompt, user_prompt = build_judge_prompt(
        case,
        candidate,
        include_schema=False,
    )
    request = TextGenerationRequest(
        system_prompt=system_prompt,
        messages=[ChatMessage(role="user", content=user_prompt)],
        temperature=0.0,
        max_output_tokens=384,
        request_id=f"judge_v2_compat_{model}_{int(time.time())}",
    )
    started = time.perf_counter()
    result: dict[str, Any] = {
        "model": model,
        "status": "failed",
        "compatible": False,
        "fallbackEligible": False,
        "errorCode": None,
        "physicalRequests": 0,
        "providerRetries": 0,
        "latencyMs": None,
        "tokenUsage": _usage_dict(None),
    }
    try:
        response = await client.generate(request)
        result["physicalRequests"] = client.metrics_snapshot()["providerAttempts"]
        result["providerRetries"] = client.metrics_snapshot()["providerRetries"]
        result["tokenUsage"] = _usage_dict(response.usage)
        try:
            parse_judge_score(response.text)
        except (ValueError, TypeError) as exc:
            result["errorCode"] = f"strict_schema_response:{type(exc).__name__}"
            result["fallbackEligible"] = True
        else:
            result["status"] = "passed"
            result["compatible"] = True
    except AIError as exc:
        snapshot = client.metrics_snapshot()
        result["physicalRequests"] = snapshot["providerAttempts"]
        result["providerRetries"] = snapshot["providerRetries"]
        result["errorCode"] = str(exc.code)
        result["fallbackEligible"] = exc.code == AIErrorCode.INVALID_REQUEST
        details = exc.details if isinstance(exc.details, dict) else {}
        usage = details.get("usage")
        if isinstance(usage, dict):
            result["tokenUsage"] = {
                "promptTokens": usage.get("prompt_tokens"),
                "completionTokens": usage.get("completion_tokens"),
                "totalTokens": usage.get("total_tokens"),
            }
    finally:
        result["latencyMs"] = round((time.perf_counter() - started) * 1000, 3)
        await client.close()
    return result, bool(result["fallbackEligible"])


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds must be positive")
    api_key = os.environ.get("ARK_JUDGE_API_KEY", "").strip() or os.environ.get(
        "ARK_API_KEY", ""
    ).strip()
    if not api_key:
        raise SystemExit("ARK_API_KEY is not configured; no request was sent")
    base_url = (
        os.environ.get("ARK_JUDGE_BASE_URL", "").strip()
        or os.environ.get("ARK_BASE_URL", "").strip()
        or DEFAULT_ARK_BASE_URL
    )
    attempts: list[dict[str, Any]] = []
    primary, fallback_eligible = await _probe(
        model=PRIMARY_MODEL,
        api_key=api_key,
        base_url=base_url,
        timeout_seconds=args.timeout_seconds,
    )
    attempts.append(primary)
    if not primary["compatible"] and fallback_eligible:
        fallback, _ = await _probe(
            model=FALLBACK_MODEL,
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=args.timeout_seconds,
        )
        attempts.append(fallback)
    selected = next((item["model"] for item in attempts if item["compatible"]), None)
    report = {
        "schemaVersion": 1,
        "stage": "judge-v2-technical-compatibility",
        "createdAt": datetime.now(UTC).isoformat(),
        "status": "passed" if selected else "failed",
        "selectedModel": selected,
        "primaryModel": PRIMARY_MODEL,
        "fallbackModel": FALLBACK_MODEL,
        "apiMode": "responses",
        "baseUrlHost": ArkSettings(base_url=base_url).base_url_host,
        "rubricVersion": RUBRIC_VERSION,
        "promptSha256": judge_prompt_sha256(),
        "schemaSha256": judge_schema_sha256(),
        "temperature": 0.0,
        "thinking": "disabled",
        "store": False,
        "strictJsonSchema": True,
        "localPydanticValidation": True,
        "maxPhysicalRequests": 2,
        "physicalRequests": sum(int(item["physicalRequests"]) for item in attempts),
        "tokenUsage": {
            "promptTokens": sum(
                int(item["tokenUsage"]["promptTokens"] or 0) for item in attempts
            ),
            "completionTokens": sum(
                int(item["tokenUsage"]["completionTokens"] or 0) for item in attempts
            ),
            "totalTokens": sum(
                int(item["tokenUsage"]["totalTokens"] or 0) for item in attempts
            ),
        },
        "estimatedCostCny": None,
        "agentFuelCredits": None,
        "humanValidated": False,
        "attempts": attempts,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(_run(args))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
