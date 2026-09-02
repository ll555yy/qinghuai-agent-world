"""Production persistence/replay adapter for the eight local P0 faults."""

from __future__ import annotations

import time
from types import TracebackType
from typing import Any, Self

from sqlalchemy import delete

from benchmark.reliability.injectors import (
    FaultInjectionError,
    FaultInjector,
    FaultPlan,
)
from benchmark.reliability.runner import ReliabilityObservation
from core.backend.app.ai.decision_service import DecisionService
from core.backend.app.ai.models import TextGenerationResult
from core.backend.app.ai.protocols import MemoryQuery
from core.backend.app.db.bootstrap import sync_scenario
from core.backend.app.db.models import ChapterRun
from core.backend.app.orchestration.run_service import RunService
from core.backend.app.persistence.codec import serialize_run
from core.backend.app.persistence.memory_retriever import DatabaseMemoryRetriever
from core.backend.app.persistence.sqlalchemy_repository import SQLAlchemyRunRepository
from core.backend.app.scenario.loader import ScenarioLoader


class _ProtocolFaultModel:
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.calls = 0

    async def generate(self, request: Any) -> TextGenerationResult:
        self.calls += 1
        if self.calls == 1:
            if self.kind == "timeout":
                raise TimeoutError("injected candidate timeout")
            return TextGenerationResult(text="not-json", provider="benchmark", model="fault")
        return TextGenerationResult(
            text='{"action":"wait"}', provider="benchmark", model="recovery"
        )


class _OutageEmbedding:
    model_name = "benchmark-outage"
    dimensions = 2048

    async def embed(self, text: str) -> list[float]:
        raise ConnectionError("injected embedding outage")


class _SaveFaultRepository:
    """Delegate all persistence, failing one armed save before transaction."""

    def __init__(self, delegate: SQLAlchemyRunRepository) -> None:
        self.delegate = delegate
        self.armed = False
        self.injected = False

    async def add(self, run: Any) -> None:
        await self.delegate.add(run)

    async def save(self, run: Any, *, expected_revision: int | None = None) -> int:
        if self.armed and not self.injected:
            self.injected = True
            raise ConnectionError("injected PostgreSQL disconnect before transaction")
        return await self.delegate.save(run, expected_revision=expected_revision)

    async def get(self, run_id: str) -> Any:
        return await self.delegate.get(run_id)

    async def events_after(self, run_id: str, after_seq: int = 0) -> Any:
        return await self.delegate.events_after(run_id, after_seq)

    async def revision(self, run_id: str) -> int | None:
        return await self.delegate.revision(run_id)

    async def list(self) -> Any:
        return await self.delegate.list()

    async def healthcheck(self) -> bool:
        return await self.delegate.healthcheck()

    async def close(self) -> None:
        await self.delegate.close()


class ProductionReliabilityAdapter:
    """Exercise real RunService commands against a dedicated PostgreSQL DB."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.registry = ScenarioLoader("core/scenario").load()
        self._run_ids: set[str] = set()

    async def __aenter__(self) -> Self:
        probe = self._repository()
        try:
            if not await probe.healthcheck():
                raise RuntimeError("dedicated PostgreSQL database is unavailable or not migrated")
            await sync_scenario(probe.session_factory, self.registry)
        finally:
            await probe.close()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        repository = self._repository()
        try:
            if self._run_ids:
                async with repository.session_factory() as session, session.begin():
                    await session.execute(
                        delete(ChapterRun).where(ChapterRun.run_id.in_(self._run_ids))
                    )
        finally:
            await repository.close()

    def _repository(self) -> SQLAlchemyRunRepository:
        return SQLAlchemyRunRepository(
            self.database_url, chapter_id=self.registry.chapter_id
        )

    async def _create(self, seed: int, *, repository: Any | None = None) -> tuple[Any, RunService, str]:
        repo = repository or self._repository()
        service = RunService(self.registry, repository=repo, text_model=None)
        created = await service.create_run(seed=seed)
        run_id = str(created["runId"])
        self._run_ids.add(run_id)
        return repo, service, run_id

    async def _reload(self, run_id: str) -> tuple[SQLAlchemyRunRepository, RunService]:
        repository = self._repository()
        service = RunService(self.registry, repository=repository, text_model=None)
        await service.get_run_entity(run_id)
        return repository, service

    @staticmethod
    async def _state(service: RunService, run_id: str) -> dict[str, Any]:
        return serialize_run(await service.get_run_entity(run_id))

    @staticmethod
    def _target_day(seed: int) -> int:
        return (3, 5, 7)[seed % 3]

    async def _prepare(self, service: RunService, run_id: str, plan: FaultPlan, seed: int) -> None:
        if plan.fault_id == "F5_process_restart":
            run = await service.get_run_entity(run_id)
            days = self._target_day(seed) - run.clock.current.day
            if days > 0:
                active_minutes_per_day = (
                    run.clock.active_end_minutes - run.clock.active_start_minutes
                )
                await service.advance_time(
                    run_id,
                    days * active_minutes_per_day,
                    command_id=f"reliability:{plan.fault_id}:{seed}:boundary",
                )
        elif plan.fault_id == "F7_ws_reconnect":
            await service.advance_time(
                run_id, 1, command_id=f"reliability:{plan.fault_id}:{seed}:event"
            )

    async def _finish_command(self, service: RunService, run_id: str, plan: FaultPlan, seed: int) -> dict[str, Any]:
        return await service.advance_time(
            run_id,
            1,
            command_id=f"reliability:{plan.fault_id}:{seed}:final",
        )

    async def run_control(self, seed: int, *, plan: FaultPlan) -> ReliabilityObservation:
        repository, service, run_id = await self._create(seed)
        try:
            await self._prepare(service, run_id, plan, seed)
            await self._finish_command(service, run_id, plan, seed)
            state = await self._state(service, run_id)
            return ReliabilityObservation(
                state=state,
                side_effect_ids=(f"command:{plan.fault_id}:{seed}",),
                rollback_correct=True,
                recovery_time_ms=0.0,
                metadata={"executionMode": "production_postgres", "control": True},
            )
        finally:
            await service.close()
            await repository.close()

    async def _verify_protocol_retry(self, kind: str) -> dict[str, Any]:
        model = _ProtocolFaultModel(kind)
        decision = await DecisionService(model, max_concurrency=1).daily_action("等待")
        return {"calls": model.calls, "result": decision.action, "verified": model.calls == 2}

    async def _verify_embedding_fallback(self, repository: SQLAlchemyRunRepository, run_id: str) -> dict[str, Any]:
        result = await DatabaseMemoryRetriever(
            repository.session_factory, embedding_port=_OutageEmbedding()
        ).search(
            run_id=run_id,
            owner_npc_id="npc_001",
            query=MemoryQuery(queryText="文社", actorIds=["player_001"], limit=5),
        )
        return {
            "resultCount": result.count,
            "ownerScoped": all(
                str(memory_id).startswith("memory_") for memory_id in result.memory_ids
            ),
            "verified": result.count > 0,
        }

    async def run_fault(
        self, plan: FaultPlan, seed: int, injector: FaultInjector
    ) -> ReliabilityObservation:
        started = time.perf_counter()
        metadata: dict[str, Any] = {
            "executionMode": "production_postgres",
            "faultId": plan.fault_id,
            "injectionPoint": plan.injection_point,
        }
        retry_count = 0
        rollback_correct: bool | None = True
        repository: Any
        service: RunService
        if plan.fault_id == "F4_database_disconnect":
            base = self._repository()
            wrapper = _SaveFaultRepository(base)
            repository, service, run_id = await self._create(seed, repository=wrapper)
        else:
            repository, service, run_id = await self._create(seed)
        try:
            await self._prepare(service, run_id, plan, seed)
            if plan.fault_id == "F1_model_timeout":
                metadata["protocolProbe"] = await self._verify_protocol_retry("timeout")
                retry_count = 1
                await self._finish_command(service, run_id, plan, seed)
            elif plan.fault_id == "F2_invalid_schema":
                metadata["protocolProbe"] = await self._verify_protocol_retry("schema")
                retry_count = 1
                await self._finish_command(service, run_id, plan, seed)
            elif plan.fault_id == "F3_embedding_outage":
                metadata["retrievalProbe"] = await self._verify_embedding_fallback(
                    repository, run_id
                )
                retry_count = 1
                await self._finish_command(service, run_id, plan, seed)
            elif plan.fault_id == "F4_database_disconnect":
                before = await self._state(service, run_id)
                repository.armed = True
                try:
                    await self._finish_command(service, run_id, plan, seed)
                except ConnectionError:
                    retry_count = 1
                await service.close()
                await repository.close()
                repository, service = await self._reload(run_id)
                reloaded = await self._state(service, run_id)
                rollback_correct = (
                    before["eventSeq"] == reloaded["eventSeq"]
                    and before["stateVersion"] == reloaded["stateVersion"]
                )
                await self._finish_command(service, run_id, plan, seed)
                metadata["injectionLevel"] = "repository_save_before_transaction"
            elif plan.fault_id == "F5_process_restart":
                await service.close()
                await repository.close()
                repository, service = await self._reload(run_id)
                retry_count = 1
                await self._finish_command(service, run_id, plan, seed)
                metadata["restartDay"] = self._target_day(seed)
            elif plan.fault_id == "F6_duplicate_command":
                first = await self._finish_command(service, run_id, plan, seed)
                before_duplicate = (await self._state(service, run_id))["eventSeq"]
                second = await self._finish_command(service, run_id, plan, seed)
                after_duplicate = (await self._state(service, run_id))["eventSeq"]
                rollback_correct = before_duplicate == after_duplicate and first == second
                metadata["cachedResultMatched"] = first == second
            elif plan.fault_id == "F7_ws_reconnect":
                events = (await service.get_events(run_id, 0))["events"]
                cutoff = int(events[len(events) // 2]["eventSeq"]) if events else 0
                replay = (await service.get_events(run_id, cutoff))["events"]
                replay_seqs = [int(item["eventSeq"]) for item in replay]
                metadata["replayStrictlyIncreasing"] = replay_seqs == sorted(set(replay_seqs))
                metadata["replayAfterSeq"] = cutoff
                retry_count = 1
                await self._finish_command(service, run_id, plan, seed)
            else:  # F8: commit succeeds, delivery fails, process reloads and retries.
                try:
                    await injector.ainvoke(
                        self._finish_command,
                        service,
                        run_id,
                        plan,
                        seed,
                        point=plan.injection_point,
                    )
                except FaultInjectionError:
                    retry_count = 1
                committed_seq = (await self._state(service, run_id))["eventSeq"]
                await service.close()
                await repository.close()
                repository, service = await self._reload(run_id)
                await self._finish_command(service, run_id, plan, seed)
                rollback_correct = (await self._state(service, run_id))["eventSeq"] == committed_seq
            state = await self._state(service, run_id)
            probes_ok = all(
                bool(value.get("verified", True))
                for key, value in metadata.items()
                if key.endswith("Probe") and isinstance(value, dict)
            )
            recovered = bool(rollback_correct is not False and probes_ok)
            return ReliabilityObservation(
                state=state,
                recovered=recovered,
                retry_count=retry_count,
                side_effect_ids=(f"command:{plan.fault_id}:{seed}",),
                rollback_correct=rollback_correct,
                recovery_time_ms=(time.perf_counter() - started) * 1000,
                infra_valid=True,
                failure_code=None if recovered else "recovery_assertion_failed",
                metadata=metadata,
            )
        finally:
            await service.close()
            await repository.close()


__all__ = ["ProductionReliabilityAdapter"]
