"""Offline-by-default Ark six-protocol acceptance check.

The command never sends a request unless ``--live`` is supplied explicitly.
Even in live mode it sends only six fixed, non-game prompts and emits a safe
JSON report containing no prompt, response text, API key, or private context.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_ROOT / ".env", override=False)
if __package__ in {None, ""}:
    sys.path.insert(0, str(_ROOT))

from core.backend.app.ai.ark_client import ArkClient  # noqa: E402
from core.backend.app.ai.decision_service import (  # noqa: E402
    PROTOCOL_RULES,
    TIME_POLICY_RULE,
    extract_json_object,
)
from core.backend.app.ai.errors import AIError  # noqa: E402
from core.backend.app.ai.models import (  # noqa: E402
    ChatMessage,
    TextGenerationRequest,
    TokenUsage,
)
from core.backend.app.ai.protocols import (  # noqa: E402
    ChatDecision,
    DailyActionDecision,
    ExitConsolidation,
    InvitationDecision,
    SegmentSummary,
    SpeechGeneration,
)

REPORT_NAME = "ark_six_protocol_acceptance"
MAX_FORMAT_ATTEMPTS = 2

_PROTOCOLS: tuple[tuple[str, type[BaseModel], str], ...] = (
    (
        "DailyActionDecision",
        DailyActionDecision,
        "固定验收输入：没有合适的聊天对象，请返回 wait。",
    ),
    (
        "InvitationDecision",
        InvitationDecision,
        "固定验收输入：这是一个不应接受的普通邀请，请返回 refuse。",
    ),
    (
        "ChatDecision",
        ChatDecision,
        "固定验收输入：没有新消息需要处理，请返回 decided + wait，不要召回记忆。",
    ),
    (
        "SpeechGeneration",
        SpeechGeneration,
        "固定验收输入：生成一句不包含身份、ID 或内部信息的简短中文回应。",
    ),
    (
        "SegmentSummary",
        SegmentSummary,
        "固定验收输入：没有聊天消息需要摘要，请返回空数组对象。",
    ),
    (
        "ExitConsolidation",
        ExitConsolidation,
        "固定验收输入：没有聊天消息需要沉淀，请返回所有空数组对象。",
    ),
)


def _system_prompt(protocol: str, model_type: type[BaseModel]) -> str:
    schema = json.dumps(model_type.model_json_schema(), ensure_ascii=False)
    time_policy = "" if protocol == "SegmentSummary" else TIME_POLICY_RULE
    return (
        "你是青槐老巷世界的后台决策模型。这是本机接入验收，不包含任何游戏私密内容。"
        "只输出一个符合给定 JSON Schema 的 JSON 对象，不要输出解释、Markdown 或额外字段。\n"
        f"协议={protocol}\n"
        f"规则={PROTOCOL_RULES[protocol]}\n"
        f"时间政策={time_policy}\n"
        f"Schema={schema}"
    )


def _request(
    protocol: str,
    model_type: type[BaseModel],
    user_prompt: str,
    request_id: str,
) -> TextGenerationRequest:
    return TextGenerationRequest(
        system_prompt=_system_prompt(protocol, model_type),
        messages=[ChatMessage(role="user", content=user_prompt)],
        temperature=0,
        max_output_tokens=256,
        request_id=request_id,
    )


def _add_usage(total: dict[str, int], usage: TokenUsage | None) -> bool:
    if usage is None:
        return False
    seen = False
    for field_name, report_name in (
        ("prompt_tokens", "promptTokens"),
        ("completion_tokens", "completionTokens"),
        ("total_tokens", "totalTokens"),
    ):
        value = getattr(usage, field_name)
        if isinstance(value, int):
            total[report_name] = total.get(report_name, 0) + value
            seen = True
    return seen


async def _check_protocol(
    client: ArkClient,
    protocol: str,
    model_type: type[BaseModel],
    user_prompt: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    request_ids: list[str] = []
    usage_totals: dict[str, int] = {}
    usage_seen = False
    format_retries = 0
    error_code: str | None = None
    provider_request_id: str | None = None

    for attempt in range(MAX_FORMAT_ATTEMPTS):
        request_id = f"ark-acceptance-{protocol}-{uuid.uuid4().hex}"
        request_ids.append(request_id)
        try:
            result = await client.generate(
                _request(protocol, model_type, user_prompt, request_id)
            )
        except AIError as exc:
            error_code = str(exc.code)
            break
        except Exception:
            error_code = "unexpected_provider_error"
            break

        usage_seen = _add_usage(usage_totals, result.usage) or usage_seen
        provider_request_id = result.provider_request_id
        try:
            model_type.model_validate(extract_json_object(result.text))
        except (ValidationError, ValueError, json.JSONDecodeError):
            if attempt + 1 < MAX_FORMAT_ATTEMPTS:
                format_retries += 1
                continue
            error_code = "invalid_protocol_output"
            break

        return {
            "protocol": protocol,
            "requestId": request_ids[0],
            "providerRequestId": provider_request_id,
            "attemptCount": len(request_ids),
            "formatRetries": format_retries,
            "success": True,
            "latencyMs": int((time.perf_counter() - started) * 1000),
            "usage": usage_totals if usage_seen else None,
            "errorCode": None,
        }

    return {
        "protocol": protocol,
        "requestId": request_ids[0],
        "providerRequestId": provider_request_id,
        "attemptCount": len(request_ids),
        "formatRetries": format_retries,
        "success": False,
        "latencyMs": int((time.perf_counter() - started) * 1000),
        "usage": usage_totals if usage_seen else None,
        "errorCode": error_code or "unexpected_check_failure",
    }


async def run_check(*, live: bool) -> tuple[dict[str, Any], int]:
    """Run the dry-run or explicitly enabled live check."""

    if not live:
        return (
            {
                "tool": REPORT_NAME,
                "live": False,
                "requestSent": False,
                "success": True,
                "status": "dry_run",
                "checks": [],
            },
            0,
        )

    # Only inspect whether a key is present.  The value is never copied into
    # the report, logs, exception text, or a command-line argument.
    if not os.environ.get("ARK_API_KEY", "").strip():
        return (
            {
                "tool": REPORT_NAME,
                "live": True,
                "requestSent": False,
                "success": False,
                "status": "not_configured",
                "checks": [],
            },
            2,
        )

    try:
        client = ArkClient()
    except Exception:
        return (
            {
                "tool": REPORT_NAME,
                "live": True,
                "requestSent": False,
                "success": False,
                "status": "client_init_failed",
                "checks": [],
            },
            1,
        )
    checks: list[dict[str, Any]] = []
    close_error = False
    try:
        for protocol, model_type, user_prompt in _PROTOCOLS:
            checks.append(await _check_protocol(client, protocol, model_type, user_prompt))
    finally:
        try:
            await client.close()
        except Exception:
            close_error = True

    success = all(check["success"] for check in checks) and not close_error
    status_method = getattr(client, "status", None)
    status = (
        status_method()
        if callable(status_method)
        else {"provider": "unknown", "model": "unknown"}
    )
    metrics_method = getattr(client, "metrics_snapshot", None)
    provider_metrics = metrics_method() if callable(metrics_method) else None
    return (
        {
            "tool": REPORT_NAME,
            "live": True,
            "requestSent": True,
            "success": success,
            "status": "completed" if success else "failed",
            "provider": status["provider"],
            "model": status["model"],
            "providerMetrics": provider_metrics,
            "errorCode": "client_close_failed" if close_error else None,
            "checks": checks,
        },
        0 if success else 1,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="send six fixed checks to Ark; without this flag no request is sent",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report, exit_code = asyncio.run(run_check(live=args.live))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
