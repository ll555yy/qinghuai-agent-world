"""Deterministic fault plans and injectable operation wrappers."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    yaml = None  # type: ignore[assignment]
    _YAML_IMPORT_ERROR = exc


FAULT_IDS = frozenset(
    {
        "F1_model_timeout",
        "F2_invalid_schema",
        "F3_embedding_outage",
        "F4_database_disconnect",
        "F5_process_restart",
        "F6_duplicate_command",
        "F7_ws_reconnect",
        "F8_lost_response",
    }
)


def _get(value: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in value:
            return value[name]
    return default


@dataclass(frozen=True, slots=True)
class FaultPlan:
    fault_id: str
    name: str
    description: str
    injection_point: str
    trigger_call: int = 1
    max_injections: int = 1
    retryable: bool = True
    after_commit: bool = False
    attempts: int = 10
    expected_recovery: str = "retry_or_fallback"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, index: int = 0) -> FaultPlan:
        fault_id = str(_get(value, "faultId", "fault_id", "id", default="")).strip()
        if not fault_id:
            raise ValueError(f"fault {index + 1}: faultId is required")
        return cls(
            fault_id=fault_id,
            name=str(_get(value, "name", "title", default=fault_id)),
            description=str(_get(value, "description", "failure", default="")),
            injection_point=str(_get(value, "injectionPoint", "injection_point", "point", default="")),
            trigger_call=int(_get(value, "triggerCall", "trigger_call", default=1)),
            max_injections=int(_get(value, "maxInjections", "max_injections", default=1)),
            retryable=bool(_get(value, "retryable", default=True)),
            after_commit=bool(_get(value, "afterCommit", "after_commit", default=False)),
            attempts=int(_get(value, "attempts", default=10)),
            expected_recovery=str(_get(value, "expectedRecovery", "expected_recovery", default="retry_or_fallback")),
            metadata=dict(_get(value, "metadata", default={}) or {}),
        )


def validate_fault_plans(plans: list[FaultPlan] | tuple[FaultPlan, ...], *, require_count: bool = False) -> tuple[FaultPlan, ...]:
    if require_count and len(plans) != 8:
        raise ValueError(f"expected exactly 8 fault plans, got {len(plans)}")
    ids = [plan.fault_id for plan in plans]
    if len(set(ids)) != len(ids):
        raise ValueError("fault plan IDs must be unique")
    for plan in plans:
        if plan.fault_id not in FAULT_IDS:
            raise ValueError(f"unknown fault plan {plan.fault_id!r}")
        if not plan.injection_point:
            raise ValueError(f"{plan.fault_id}: injectionPoint is required")
        if plan.trigger_call < 1 or plan.max_injections < 1:
            raise ValueError(f"{plan.fault_id}: trigger/max injections must be positive")
        if plan.attempts < 1:
            raise ValueError(f"{plan.fault_id}: attempts must be positive")
    return tuple(plans)


FAULTS_PATH = Path(__file__).with_name("faults.yaml")


def load_fault_plans(path: str | Path | None = None, *, require_count: bool = True) -> tuple[FaultPlan, ...]:
    if yaml is None:  # pragma: no cover
        raise RuntimeError("PyYAML is required to load fault plans") from _YAML_IMPORT_ERROR
    source = Path(path) if path is not None else FAULTS_PATH
    try:
        payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read fault plan file: {source}") from exc
    if not isinstance(payload, Mapping):
        raise TypeError("fault YAML root must be a mapping")
    version = str(_get(payload, "schemaVersion", "schema_version", default="1"))
    if version not in {"1", "1.0"}:
        raise ValueError(f"unsupported fault schema version {version!r}")
    raw_plans = _get(payload, "faults", "plans", default=())
    if not isinstance(raw_plans, list):
        raise TypeError("fault YAML faults must be a list")
    plans = tuple(FaultPlan.from_mapping(item, index=index) for index, item in enumerate(raw_plans) if isinstance(item, Mapping))
    if len(plans) != len(raw_plans):
        raise ValueError("every fault plan must be a mapping")
    return validate_fault_plans(plans, require_count=require_count)


class FaultInjectionError(RuntimeError):
    """Raised by an injector at a declared operation boundary."""

    def __init__(self, plan: FaultPlan, *, message: str | None = None) -> None:
        self.plan = plan
        self.fault_id = plan.fault_id
        self.code = plan.fault_id
        self.retryable = plan.retryable
        self.after_commit = plan.after_commit
        self.injection_point = plan.injection_point
        super().__init__(message or f"injected {plan.fault_id} at {plan.injection_point}")


InjectedFault = FaultInjectionError


class ModelTimeoutFault(FaultInjectionError):
    pass


class InvalidSchemaFault(FaultInjectionError):
    pass


class EmbeddingOutageFault(FaultInjectionError):
    pass


class DatabaseDisconnectFault(FaultInjectionError):
    pass


class ProcessRestartFault(FaultInjectionError):
    pass


class DuplicateCommandFault(FaultInjectionError):
    pass


class WebSocketReconnectFault(FaultInjectionError):
    pass


class LostResponseFault(FaultInjectionError):
    pass


_FAULT_EXCEPTION_TYPES: dict[str, type[FaultInjectionError]] = {
    "F1_model_timeout": ModelTimeoutFault,
    "F2_invalid_schema": InvalidSchemaFault,
    "F3_embedding_outage": EmbeddingOutageFault,
    "F4_database_disconnect": DatabaseDisconnectFault,
    "F5_process_restart": ProcessRestartFault,
    "F6_duplicate_command": DuplicateCommandFault,
    "F7_ws_reconnect": WebSocketReconnectFault,
    "F8_lost_response": LostResponseFault,
}


class FaultInjector:
    """Apply one deterministic fault exactly at its configured call.

    The wrapper knows nothing about the application.  For F6 it invokes the
    operation twice with the same arguments; idempotency belongs to the
    adapter under test.  F8 invokes once, then raises after commit so a retry
    can exercise the same idempotency path.
    """

    def __init__(self, plan: FaultPlan, *, seed: int = 0, restart_callback: Callable[[], Any] | None = None) -> None:
        self.plan = plan
        self.seed = seed
        self.restart_callback = restart_callback
        self._calls: dict[str, int] = {}
        self._injected = 0
        self._retries = 0
        self._lock = RLock()

    @property
    def injected(self) -> int:
        return self._injected

    @property
    def retries(self) -> int:
        return self._retries

    def should_inject(self, point: str) -> bool:
        with self._lock:
            call_number = self._calls.get(point, 0) + 1
            self._calls[point] = call_number
            if point != self.plan.injection_point:
                return False
            if call_number < self.plan.trigger_call or self._injected >= self.plan.max_injections:
                return False
            self._injected += 1
            return True

    def _fault(self) -> FaultInjectionError:
        fault_type = _FAULT_EXCEPTION_TYPES.get(self.plan.fault_id, FaultInjectionError)
        return fault_type(self.plan)

    def invoke(self, operation: Callable[..., Any], *args: Any, point: str | None = None, **kwargs: Any) -> Any:
        operation_point = point or self.plan.injection_point
        if not self.should_inject(operation_point):
            return operation(*args, **kwargs)
        if self.plan.fault_id == "F6_duplicate_command":
            first = operation(*args, **kwargs)
            try:
                return operation(*args, **kwargs)
            except Exception:  # noqa: BLE001 - duplicate transport failure is the injected subject
                # Preserve the committed first result: an idempotent endpoint
                # should return it for the duplicate command.
                return first
        if self.plan.fault_id == "F8_lost_response":
            operation(*args, **kwargs)
            raise self._fault()
        if self.plan.fault_id == "F5_process_restart" and self.restart_callback is not None:
            self.restart_callback()
        raise self._fault()

    async def ainvoke(self, operation: Callable[..., Any], *args: Any, point: str | None = None, **kwargs: Any) -> Any:
        async def run() -> Any:
            value = operation(*args, **kwargs)
            return await value if inspect.isawaitable(value) else value

        operation_point = point or self.plan.injection_point
        if not self.should_inject(operation_point):
            return await run()
        if self.plan.fault_id == "F6_duplicate_command":
            first = await run()
            try:
                return await run()
            except Exception:  # noqa: BLE001 - duplicate transport failure is the injected subject
                return first
        if self.plan.fault_id == "F8_lost_response":
            await run()
            raise self._fault()
        if self.plan.fault_id == "F5_process_restart" and self.restart_callback is not None:
            result = self.restart_callback()
            if inspect.isawaitable(result):
                await result
        raise self._fault()

    def invoke_with_retries(
        self,
        operation: Callable[..., Any],
        *args: Any,
        point: str | None = None,
        max_retries: int = 2,
        on_failure: Callable[[FaultInjectionError], Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        attempts = 0
        while True:
            try:
                return self.invoke(operation, *args, point=point, **kwargs)
            except FaultInjectionError as exc:
                if on_failure is not None:
                    on_failure(exc)
                if not exc.retryable or attempts >= max_retries:
                    raise
                attempts += 1
                self._retries += 1

    async def ainvoke_with_retries(
        self,
        operation: Callable[..., Any],
        *args: Any,
        point: str | None = None,
        max_retries: int = 2,
        on_failure: Callable[[FaultInjectionError], Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        attempts = 0
        while True:
            try:
                return await self.ainvoke(operation, *args, point=point, **kwargs)
            except FaultInjectionError as exc:
                if on_failure is not None:
                    result = on_failure(exc)
                    if inspect.isawaitable(result):
                        await result
                if not exc.retryable or attempts >= max_retries:
                    raise
                attempts += 1
                self._retries += 1

    # Friendly aliases for adapters that call the boundary ``inject``.
    inject = invoke
    ainject = ainvoke

    def snapshot(self) -> dict[str, Any]:
        return {
            "faultId": self.plan.fault_id,
            "seed": self.seed,
            "calls": dict(self._calls),
            "injected": self._injected,
            "retries": self._retries,
        }


__all__ = [
    "FAULT_IDS",
    "DatabaseDisconnectFault",
    "DuplicateCommandFault",
    "EmbeddingOutageFault",
    "FaultInjectionError",
    "FaultInjector",
    "FaultPlan",
    "InjectedFault",
    "InvalidSchemaFault",
    "LostResponseFault",
    "ModelTimeoutFault",
    "ProcessRestartFault",
    "WebSocketReconnectFault",
    "load_fault_plans",
    "validate_fault_plans",
]
