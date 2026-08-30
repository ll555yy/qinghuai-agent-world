"""Versioned, repository-registered semantic Judge profiles."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

_PROFILE_ID = re.compile(r"judge-v[12](?:-[a-z0-9-]+)?\Z")
_PROFILE_DIR = Path(__file__).resolve().parents[3] / "evaluation" / "judge_profiles"


class JudgeProfile(BaseModel):
    """Frozen execution contract; secrets and endpoint paths are excluded."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: int = Field(ge=1, le=1)
    profileId: str
    profileVersion: int = Field(ge=1, le=2)
    status: str
    provider: str
    model: str
    apiMode: str
    rubricVersion: str
    promptSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schemaSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    temperature: float
    thinking: str
    store: bool
    strictJsonSchema: bool
    formatRetries: int = Field(ge=0, le=1)
    providerRetries: int = Field(ge=0, le=1)
    timeoutSeconds: float = Field(gt=0)
    inputCnyPerMillion: float = Field(ge=0)
    outputCnyPerMillion: float = Field(ge=0)
    registeredAt: str
    humanValidated: bool

    @model_validator(mode="after")
    def validate_contract(self) -> JudgeProfile:
        if not _PROFILE_ID.fullmatch(self.profileId):
            raise ValueError("profileId must be a registered Judge v1/v2 identifier")
        if self.profileVersion not in {1, 2}:
            raise ValueError("only Judge profile versions 1 and 2 are supported")
        if self.apiMode != "responses":
            raise ValueError("Judge profiles must use the Responses API")
        if self.temperature != 0:
            raise ValueError("Judge profiles must freeze temperature=0")
        if self.thinking != "disabled" or self.store:
            raise ValueError("Judge profiles must disable thinking and storage")
        if not self.strictJsonSchema:
            raise ValueError("Judge profiles require strict JSON Schema")
        if self.humanValidated:
            raise ValueError("automated Judge profiles cannot claim human validation")
        return self


def registered_judge_profile_ids() -> tuple[str, ...]:
    if not _PROFILE_DIR.exists():
        return ()
    return tuple(sorted(path.stem for path in _PROFILE_DIR.glob("judge-v*.json")))


def load_judge_profile(profile_id: str) -> JudgeProfile:
    """Load one exact registered profile; paths and arbitrary models are rejected."""

    if not _PROFILE_ID.fullmatch(profile_id):
        raise ValueError(f"unknown Judge profile: {profile_id}")
    path = _PROFILE_DIR / f"{profile_id}.json"
    if not path.is_file():
        raise ValueError(f"unknown Judge profile: {profile_id}")
    profile = JudgeProfile.model_validate_json(path.read_text(encoding="utf-8"))
    if profile.profileId != profile_id or profile.profileVersion != int(profile_id[7]):
        raise ValueError(f"Judge profile identity mismatch: {profile_id}")

    # Detect a prompt/schema code change before any provider request is made.
    from .judge import judge_prompt_sha256, judge_schema_sha256

    if profile.promptSha256 != judge_prompt_sha256():
        raise ValueError(f"Judge profile prompt hash mismatch: {profile_id}")
    if profile.schemaSha256 != judge_schema_sha256():
        raise ValueError(f"Judge profile schema hash mismatch: {profile_id}")
    return profile


__all__ = [
    "JudgeProfile",
    "load_judge_profile",
    "registered_judge_profile_ids",
]
