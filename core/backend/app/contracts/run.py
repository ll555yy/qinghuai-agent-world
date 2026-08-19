"""Run and time command contracts."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from .common import ContractModel


class CreateRunRequest(ContractModel):
    agenda_id: str | None = Field(default=None, alias="agendaId")
    seed: int | None = None


class AdvanceTimeRequest(ContractModel):
    virtual_minutes: int = Field(alias="virtualMinutes", gt=0)
    command_id: str | None = Field(default=None, alias="commandId")


class EventsResponse(ContractModel):
    run_id: str = Field(alias="runId")
    after_seq: int = Field(alias="afterSeq")
    events: list[dict[str, Any]]


class WorldStepRequest(ContractModel):
    real_seconds: int = Field(alias="realSeconds", gt=0)
    command_id: str | None = Field(default=None, alias="commandId")
