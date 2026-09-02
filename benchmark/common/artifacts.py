from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .models import AttemptRecord, ExperimentManifest, ResumeMetrics

ARTIFACT_FILENAMES = (
    "manifest.json",
    "manifest.sha256",
    "attempts.jsonl",
    "per-case.jsonl",
    "aggregate.json",
    "report.md",
    "failure-analysis.md",
    "resume-metrics.json",
    "budget-ledger.jsonl",
    "README.md",
)


def _jsonable(value: Any) -> Any:
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if is_dataclass(value):
        return asdict(value)
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


class ArtifactStore:
    def __init__(self, root: str | Path, experiment_id: str) -> None:
        if not experiment_id or any(part in experiment_id for part in ("/", "\\", "..")):
            raise ValueError("experiment_id must be a safe directory name")
        self.root = Path(root).resolve()
        self.directory = (self.root / experiment_id).resolve()
        if self.root not in self.directory.parents:
            raise ValueError("experiment directory escaped artifact root")

    def initialize(self, manifest: ExperimentManifest) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "raw-traces").mkdir(exist_ok=True)
        manifest_path = self.directory / "manifest.json"
        payload = json.dumps(manifest.as_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        if manifest_path.exists() and manifest_path.read_text(encoding="utf-8") != payload:
            raise ValueError("manifest is immutable once an experiment exists")
        manifest_path.write_text(payload, encoding="utf-8")
        (self.directory / "manifest.sha256").write_text(
            sha256_file(manifest_path) + "\n", encoding="ascii"
        )
        for filename in ("attempts.jsonl", "per-case.jsonl", "budget-ledger.jsonl"):
            (self.directory / filename).touch(exist_ok=True)
        return self.directory

    def append_jsonl(self, filename: str, value: Any) -> None:
        if filename not in {"attempts.jsonl", "per-case.jsonl", "budget-ledger.jsonl"}:
            raise ValueError("unsupported append-only artifact")
        with (self.directory / filename).open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(canonical_json(value) + "\n")

    def append_attempt(self, attempt: AttemptRecord) -> None:
        self.append_jsonl("attempts.jsonl", attempt)

    def write_raw_traces(self, rows: Iterable[Mapping[str, Any]]) -> None:
        """Write one deterministic, reviewable trace artifact per result row."""

        trace_directory = self.directory / "raw-traces"
        trace_directory.mkdir(exist_ok=True)
        for index, row in enumerate(rows):
            identity = next(
                (
                    row.get(name)
                    for name in ("taskId", "caseId", "faultId", "pairedGroupId")
                    if row.get(name)
                ),
                f"row-{index:05d}",
            )
            condition = row.get("conditionId") or row.get("configId") or "default"
            seed = row.get("seed", 0)
            stem = re.sub(
                r"[^A-Za-z0-9_.-]+",
                "-",
                f"{index:05d}-{identity}-{condition}-seed-{seed}",
            ).strip("-.")
            (trace_directory / f"{stem}.json").write_text(
                json.dumps(dict(row), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )

    def completed_attempt_keys(self) -> set[tuple[str, str, int, int]]:
        path = self.directory / "attempts.jsonl"
        if not path.exists():
            return set()
        completed: set[tuple[str, str, int, int]] = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if value.get("status") == "completed":
                completed.add(
                    (
                        value["case_id"],
                        value["condition_id"],
                        int(value["seed"]),
                        int(value.get("attempt_index", 0)),
                    )
                )
        return completed

    def write_report(
        self,
        *,
        per_cases: Iterable[Mapping[str, Any]],
        aggregate: Mapping[str, Any],
        report_markdown: str,
        failure_markdown: str,
        resume_metrics: ResumeMetrics,
        readme: str,
    ) -> None:
        rows = [dict(value) for value in per_cases]
        self.write_raw_traces(rows)
        cases_path = self.directory / "per-case.jsonl"
        cases_path.write_text(
            "".join(canonical_json(value) + "\n" for value in rows), encoding="utf-8"
        )
        (self.directory / "aggregate.json").write_text(
            json.dumps(dict(aggregate), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        (self.directory / "report.md").write_text(report_markdown.rstrip() + "\n", encoding="utf-8")
        (self.directory / "failure-analysis.md").write_text(
            failure_markdown.rstrip() + "\n", encoding="utf-8"
        )
        (self.directory / "resume-metrics.json").write_text(
            json.dumps(resume_metrics.as_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        (self.directory / "README.md").write_text(readme.rstrip() + "\n", encoding="utf-8")


__all__ = ["ARTIFACT_FILENAMES", "ArtifactStore", "canonical_json", "sha256_file"]
