"""Run a bounded, content-free Ark smoke test for parallel chat rounds.

The command is inert unless ``--live`` is supplied.  It uses the in-memory
repository, never writes prompts or generated text to its report, and caps the
number of logical model calls made through the production decision pipeline.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_ROOT / ".env", override=False)
sys.path.insert(0, str(_ROOT))

from core.backend.app.ai.ark_client import ArkClient  # noqa: E402
from core.backend.app.domain.conversation import Conversation  # noqa: E402
from core.backend.app.orchestration.run_service import RunService  # noqa: E402
from core.backend.app.scenario.loader import ScenarioLoader  # noqa: E402


class _MeasuredBoundedModel:
    configured = True

    def __init__(self, delegate: ArkClient, maximum: int) -> None:
        self.delegate = delegate
        self.maximum = maximum
        self.calls = 0
        self.active = 0
        self.peak = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.protocol_counts: Counter[str] = Counter()
        self.spans: list[dict[str, Any]] = []
        self.errors: Counter[str] = Counter()

    async def generate(self, request: Any) -> Any:
        if self.calls >= self.maximum:
            self.errors["logical_budget_exhausted"] += 1
            raise RuntimeError("real chat model-call budget exhausted")
        self.calls += 1
        protocol = request.system_prompt.split("协议=", 1)[1].splitlines()[0]
        payload = json.loads(request.messages[0].content)
        context = payload.get("context", {})
        span = {
            "protocol": protocol,
            "conversationId": context.get("conversationId"),
            "trigger": context.get("trigger"),
            "startedAtMs": int(time.perf_counter() * 1000),
            "endedAtMs": None,
        }
        self.protocol_counts[protocol] += 1
        self.spans.append(span)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            result = await self.delegate.generate(request)
            usage = result.usage
            if usage is not None:
                self.prompt_tokens += int(usage.prompt_tokens or 0)
                self.completion_tokens += int(usage.completion_tokens or 0)
                self.total_tokens += int(usage.total_tokens or 0)
            return result
        except Exception as exc:
            code = getattr(exc, "code", None)
            label = f"{type(exc).__name__}:{code}" if code else type(exc).__name__
            self.errors[label] += 1
            raise
        finally:
            self.active -= 1
            span["endedAtMs"] = int(time.perf_counter() * 1000)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--seed", type=int, default=20260826)
    parser.add_argument("--max-model-calls", type=int, default=40)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("simulation_reports/real_message_rounds_smoke.json"),
    )
    return parser.parse_args()


def _install_conversation(
    service: RunService,
    run: Any,
    participants: list[str],
) -> Conversation:
    conversation_id, creation_seq = run.next_conversation_identity()
    conversation = Conversation(conversation_id, creation_seq, list(participants))
    run.conversations[conversation_id] = conversation
    run.messages[conversation_id] = []
    run.segments[conversation_id] = [
        {
            "segmentId": run.next_segment_identity(),
            "participants": list(participants),
            "startedAt": run.clock.as_dict()["label"],
            "summary": None,
            "summaryThroughMessageId": None,
        }
    ]
    run.conversation_drafts[conversation_id] = {}
    for actor_id in participants:
        run.actor_states[actor_id]["status"] = "chatting"
        actor = service.registry.actor(actor_id)
        if actor is not None and actor.kind == "npc":
            run.conversation_drafts[conversation_id][actor_id] = {
                "goalUpdates": {},
                "relationshipUpdates": [],
                "pendingGoals": [],
                "chapterEffects": [],
            }
        run.memory_cache[(conversation_id, actor_id)] = set()
    service._round_state_locked(run, conversation)
    return conversation


def _has_cross_conversation_overlap(spans: list[dict[str, Any]]) -> bool:
    decision_spans = [
        span
        for span in spans
        if span["protocol"] == "ChatDecision"
        and span["conversationId"] is not None
        and span["endedAtMs"] is not None
    ]
    return any(
        left["conversationId"] != right["conversationId"]
        and left["startedAtMs"] < right["endedAtMs"]
        and right["startedAtMs"] < left["endedAtMs"]
        for index, left in enumerate(decision_spans)
        for right in decision_spans[index + 1 :]
    )


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    registry = ScenarioLoader(_ROOT / "core" / "scenario").load()
    client = ArkClient()
    model = _MeasuredBoundedModel(client, args.max_model_calls)
    # Run creation may execute a scheduled Day1 action. Keep that setup
    # deterministic, then attach the real model only for the chat smoke.
    service = RunService(
        registry,
        text_model=None,
        seed=args.seed,
        chat_cooldown_seconds=0.5,
        chat_publish_delay_min_seconds=0.05,
        chat_publish_delay_max_seconds=0.08,
        chat_npc_only_safety_rounds=1,
    )
    started = time.perf_counter()
    error_code: str | None = None
    run = None
    first = None
    second = None
    first_trigger_id = ""
    cooldown_conversation_id: str | None = None
    try:
        created = await service.create_run(seed=args.seed)
        run = await service.get_run_entity(created["runId"])
        service.text_model = model
        service.decisions.model = model
        async with run.lock:
            first = _install_conversation(
                service,
                run,
                [registry.player_actor_id, "npc_001", "npc_002"],
            )
            second = _install_conversation(
                service,
                run,
                ["npc_003", "npc_004", "npc_005"],
            )
            first_names = [registry.actor(npc_id).name for npc_id in ("npc_001", "npc_002")]
            second_names = [registry.actor(npc_id).name for npc_id in ("npc_004", "npc_005")]
            first_message = service._write_message_locked(
                run,
                first,
                registry.player_actor_id,
                f"{first_names[0]}和{first_names[1]}，请你们分别明确回答：接下来怎样合作推进书店计划？",
            )
            second_message = service._write_message_locked(
                run,
                second,
                "npc_003",
                f"{second_names[0]}和{second_names[1]}，请分别说清最后一项建议；说完我们就结束今天的讨论。",
            )
            first_trigger_id = str(first_message["messageId"])
            service._queue_message_round_locked(run, first, [first_trigger_id])
            service._queue_message_round_locked(
                run,
                second,
                [str(second_message["messageId"])],
            )
            await service.repository.save(run)
        service._ensure_chat_task(run, first.conversation_id)
        service._ensure_chat_task(run, second.conversation_id)
        await asyncio.gather(
            service.wait_for_chat_idle(run.run_id, first.conversation_id, timeout=90),
            service.wait_for_chat_idle(run.run_id, second.conversation_id, timeout=90),
        )

        async with run.lock:
            # The first two conversations exist only to prove overlap. Stop
            # their autonomous tails before isolating the cooldown check.
            for conversation in (first, second):
                conversation.close("smoke_parallel_phase_complete")
                run.conversation_round_states.pop(conversation.conversation_id, None)
            cooldown_target = _install_conversation(
                service,
                run,
                ["npc_004", "npc_005"],
            )
            cooldown_conversation_id = cooldown_target.conversation_id
            service._write_message_locked(
                run,
                cooldown_target,
                "npc_004",
                "今天已经谈完，我没有更多内容，现在告别离开；这句话不需要回应。",
            )
            service._write_message_locked(
                run,
                cooldown_target,
                "npc_005",
                "好的，我也没有更多内容，今天到此结束；无需客套回复。",
            )
            state = service._round_state_locked(run, cooldown_target)
            state["triggerMessageIds"] = []
            state["queuedMessageIds"] = []
            state["pendingPublications"] = []
            service._enter_cooldown_locked(run, cooldown_target, state)
            await service.repository.save(run)
        service._wake_chat_worker(run.run_id, first.conversation_id)
        service._wake_chat_worker(run.run_id, second.conversation_id)
        service._ensure_chat_task(run, cooldown_conversation_id)
        await service.wait_for_chat_idle(
            run.run_id,
            cooldown_conversation_id,
            timeout=90,
            include_cooldown=True,
        )
        # A conversation becomes publicly closed before its final
        # consolidation provider call completes. Wait for the workers so the
        # acceptance report never counts an intentionally cancelled request.
        workers = list(service._chat_tasks.values())
        if workers:
            await asyncio.wait_for(
                asyncio.gather(*workers, return_exceptions=True),
                timeout=60,
            )
    except Exception as exc:
        error_code = type(exc).__name__
    finally:
        await service.close()
        await client.close()

    provider = client.metrics_snapshot()
    first_messages = run.messages.get(first.conversation_id, []) if run and first else []
    first_round_replies = [
        message
        for message in first_messages
        if first_trigger_id in message.get("replyToMessageIds", [])
        and str(message.get("authorActorId", "")).startswith("npc_")
    ]
    first_round_ids = {message.get("roundId") for message in first_round_replies}
    event_types = Counter(event.event_type for event in run.events) if run else Counter()
    cooldown_closed = bool(
        run
        and cooldown_conversation_id
        and not run.conversations[cooldown_conversation_id].is_open
    )
    final_check_calls = sum(
        1
        for span in model.spans
        if span["protocol"] == "ChatDecision" and span["trigger"] == "final_check"
    )
    gates = {
        "doubleNpcSameRound": len({item["authorActorId"] for item in first_round_replies}) >= 2
        and len(first_round_ids) == 1,
        "crossConversationDecisionOverlap": _has_cross_conversation_overlap(model.spans),
        "globalConcurrencyObserved": model.peak >= 2 and model.peak <= 6,
        "cooldownEntered": event_types["conversation_idle"] > 0,
        "finalCheckExecuted": final_check_calls > 0,
        "cooldownConversationClosed": cooldown_closed,
        "withinLogicalCallBudget": model.calls <= args.max_model_calls,
        "allPhysicalRequestsSettled": provider["providerAttempts"]
        == provider["completedRequests"]
        + provider["failedRequests"]
        + provider["providerRetries"],
        "noModelErrors": not model.errors,
        "noUnhandledError": error_code is None,
    }
    return {
        "live": True,
        "requestSent": provider["providerAttempts"] > 0,
        "status": "passed" if all(gates.values()) else "failed",
        "seed": args.seed,
        "latencyMs": int((time.perf_counter() - started) * 1000),
        "modelLogicalCalls": model.calls,
        "modelPhysicalRequests": provider["providerAttempts"],
        "modelCompletedRequests": provider["completedRequests"],
        "providerRetries": provider["providerRetries"],
        "providerFailedRequests": provider["failedRequests"],
        "observedConcurrencyPeak": model.peak,
        "protocolCounts": dict(sorted(model.protocol_counts.items())),
        "tokenUsage": {
            "promptTokens": model.prompt_tokens,
            "completionTokens": model.completion_tokens,
            "totalTokens": model.total_tokens,
        },
        "cost": None,
        "costNote": "Provider response exposes token usage but no billed amount.",
        "errors": dict(sorted(model.errors.items())),
        "rateLimited": any("rate" in key.lower() for key in model.errors),
        "finalCheckDecisionCalls": final_check_calls,
        "events": dict(sorted(event_types.items())),
        "gates": gates,
        "errorCode": error_code,
    }


async def _main() -> int:
    args = _args()
    if not args.live:
        print(json.dumps({"live": False, "requestSent": False, "status": "dry_run"}))
        return 0
    if args.max_model_calls <= 0 or args.timeout_seconds <= 0:
        raise SystemExit("model-call budget and timeout must be positive")
    if not os.environ.get("ARK_API_KEY", "").strip() or not os.environ.get(
        "ARK_MODEL", ""
    ).strip():
        raise SystemExit("Ark text model is not configured")
    report = await asyncio.wait_for(_run(args), timeout=args.timeout_seconds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
