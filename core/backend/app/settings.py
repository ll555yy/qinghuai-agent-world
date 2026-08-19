"""Small runtime settings object for the database-free first phase."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    scenario_dir: Path
    app_name: str = "Qinghuai Chat Backend"

    @classmethod
    def from_environment(cls) -> Settings:
        default_dir = Path(__file__).resolve().parents[2] / "scenario"
        configured = os.environ.get("QINGHUAI_SCENARIO_DIR")
        return cls(scenario_dir=Path(configured) if configured else default_dir)

