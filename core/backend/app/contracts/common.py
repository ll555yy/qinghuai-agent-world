"""Shared REST/WebSocket contract models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class ErrorBody(ContractModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(ContractModel):
    error: ErrorBody


class EventContract(ContractModel):
    run_id: str = Field(alias="runId")
    event_seq: int = Field(alias="eventSeq")
    state_version: int = Field(alias="stateVersion")
    event_type: str = Field(alias="eventType")
    payload: dict[str, Any]

