"""Versioned preregistration and attempt-ledger contracts for simulations.

The final seven-day matrix is intentionally described by data that can be
reviewed before a provider is contacted.  This module keeps the contract
small, dependency-free, and usable by both the runner and the evidence CLI.
Manifest integrity is an *external* digest: the digest is computed over the
canonical JSON representation and is stored in a sidecar file, never inside
the value being hashed.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

ManifestStatus = Literal[
    "not_started",
    "started",
    "completed",
    "provider_failed",
    "timeout",
    "budget_exhausted",
    "runner_failed",
]
# Public alias used by callers that think in terms of an attempt rather than
# the broader manifest document.
AttemptStatus = ManifestStatus

TERMINAL_ATTEMPT_STATUSES: frozenset[str] = frozenset(
    {
        "completed",
        "provider_failed",
        "timeout",
        "budget_exhausted",
        "runner_failed",
        "not_started",
    }
)
NON_TERMINAL_ATTEMPT_STATUSES: frozenset[str] = frozenset({"started"})
EXPECTED_MANIFEST_ROUTES: tuple[str, ...] = ("observer", "pro_lin", "pro_zhao")
DEFAULT_PREREG_SEEDS: tuple[int, ...] = tuple(range(20260840, 20260845))
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
ATTEMPT_ID_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}:[A-Za-z0-9_\-]+:[0-9]+$"
)

MANIFEST_DIR = Path(__file__).with_name("manifests")
DEFAULT_MANIFEST_PATH = MANIFEST_DIR / "final_agent_validation_v1.json"
DEFAULT_MANIFEST_SHA256_PATH = MANIFEST_DIR / "final_agent_validation_v1.sha256"
DEFAULT_MANIFEST_SCHEMA_PATH = MANIFEST_DIR / "experiment_manifest_schema_v1.json"


class ManifestValidationError(ValueError):
    """Raised when a preregistration manifest cannot be trusted."""


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize JSON deterministically for an external digest."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def canonical_manifest_sha256(manifest: dict[str, Any]) -> str:
    """Return the digest of a manifest without a self-referential hash field."""

    return sha256(canonical_json_bytes(manifest)).hexdigest()


def file_sha256(path: str | Path) -> str:
    """Hash one local file without exposing its path in an evidence report."""

    digest = sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_external_digest(path: str | Path) -> str:
    """Read a sidecar containing exactly one canonical SHA-256 digest."""

    value = Path(path).read_text(encoding="utf-8").strip().split()
    if len(value) != 1 or SHA256_RE.fullmatch(value[0]) is None:
        raise ManifestValidationError(f"invalid external manifest digest: {path}")
    return value[0]


def _require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestValidationError(f"{name} must be an object")
    return value


def _require_string(mapping: dict[str, Any], key: str, name: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ManifestValidationError(f"{name}.{key} must be a non-empty string")
    return value


def _require_digest(mapping: dict[str, Any], key: str, name: str) -> str:
    value = _require_string(mapping, key, name)
    if SHA256_RE.fullmatch(value) is None:
        raise ManifestValidationError(f"{name}.{key} must be a lowercase SHA-256")
    return value


def _require_positive_int(mapping: dict[str, Any], key: str, name: str) -> int:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ManifestValidationError(f"{name}.{key} must be a positive integer")
    return value


def _validate_created_at(value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise ManifestValidationError("createdAt must be a non-empty ISO timestamp")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ManifestValidationError("createdAt must be an ISO timestamp") from exc


def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Validate and return a preregistration manifest.

    Validation deliberately rejects duplicate, sparse, or non-contiguous seed
    plans.  It also rejects ``privateInputsUsed=true`` because the fixed route
    strategies are only valid as public-information playthroughs.
    """

    if not isinstance(manifest, dict):
        raise ManifestValidationError("manifest must be an object")
    if manifest.get("schemaVersion") != "1.0":
        raise ManifestValidationError("unsupported manifest schemaVersion")
    experiment_id = _require_string(manifest, "experimentId", "manifest")
    _validate_created_at(manifest.get("createdAt"))
    base_commit = manifest.get("preregistrationBaseCommit")
    if not isinstance(base_commit, str) or GIT_COMMIT_RE.fullmatch(base_commit) is None:
        raise ManifestValidationError(
            "preregistrationBaseCommit must be a 7-40 character hexadecimal commit"
        )

    models = _require_mapping(manifest.get("models"), "manifest.models")
    _require_string(models, "candidate", "manifest.models")
    _require_string(models, "embedding", "manifest.models")

    artifacts = _require_mapping(manifest.get("artifacts"), "manifest.artifacts")
    for artifact_name in ("promptPolicy", "scenario", "case"):
        artifact = _require_mapping(
            artifacts.get(artifact_name), f"manifest.artifacts.{artifact_name}"
        )
        _require_string(artifact, "sourceId", f"manifest.artifacts.{artifact_name}")
        _require_digest(artifact, "sha256", f"manifest.artifacts.{artifact_name}")
        _require_string(
            artifact,
            "digestAlgorithm",
            f"manifest.artifacts.{artifact_name}",
        )

    strategies = manifest.get("strategies")
    if not isinstance(strategies, list) or len(strategies) != len(EXPECTED_MANIFEST_ROUTES):
        raise ManifestValidationError("manifest.strategies must contain three strategies")
    strategy_by_id: dict[str, dict[str, Any]] = {}
    for index, raw_strategy in enumerate(strategies):
        strategy = _require_mapping(raw_strategy, f"manifest.strategies[{index}]")
        strategy_id = _require_string(strategy, "strategyId", "strategy")
        if strategy_id in strategy_by_id:
            raise ManifestValidationError(f"duplicate strategyId: {strategy_id}")
        _require_string(strategy, "kind", "strategy")
        _require_string(strategy, "version", "strategy")
        _require_digest(strategy, "sha256", "strategy")
        _require_string(strategy, "digestAlgorithm", "strategy")
        if strategy.get("privateInputsUsed") is not False:
            raise ManifestValidationError(
                f"strategy {strategy_id} must declare privateInputsUsed=false"
            )
        strategy_by_id[strategy_id] = strategy

    routes = _require_mapping(manifest.get("routes"), "manifest.routes")
    planned: list[dict[str, Any]] = []
    seen_attempt_ids: set[str] = set()
    seen_route_seeds: set[tuple[str, int]] = set()
    for route in EXPECTED_MANIFEST_ROUTES:
        route_plan = _require_mapping(routes.get(route), f"manifest.routes.{route}")
        strategy_id = _require_string(route_plan, "strategyId", f"manifest.routes.{route}")
        if strategy_id not in strategy_by_id:
            raise ManifestValidationError(f"route {route} references unknown strategy")
        seeds = route_plan.get("seeds")
        if not isinstance(seeds, list) or any(
            isinstance(seed, bool) or not isinstance(seed, int) for seed in seeds
        ):
            raise ManifestValidationError(f"manifest.routes.{route}.seeds must be integers")
        if len(seeds) != 5 or seeds != list(range(seeds[0], seeds[0] + 5)):
            raise ManifestValidationError(
                f"manifest.routes.{route}.seeds must contain five continuous seeds"
            )
        if route_plan.get("plannedRuns") != 5:
            raise ManifestValidationError(f"manifest.routes.{route}.plannedRuns must be 5")
        for seed in seeds:
            pair = (route, seed)
            if pair in seen_route_seeds:
                raise ManifestValidationError(f"duplicate planned route/seed: {route}/{seed}")
            seen_route_seeds.add(pair)
            attempt_id = make_attempt_id(experiment_id, route, seed)
            if ATTEMPT_ID_RE.fullmatch(attempt_id) is None:
                raise ManifestValidationError(f"invalid generated attempt id: {attempt_id}")
            if attempt_id in seen_attempt_ids:
                raise ManifestValidationError(f"duplicate generated attempt id: {attempt_id}")
            seen_attempt_ids.add(attempt_id)
            planned.append(
                {
                    "attemptId": attempt_id,
                    "experimentId": experiment_id,
                    "route": route,
                    "seed": seed,
                    "strategyId": strategy_id,
                    "planned": True,
                }
            )

    declared_planned = manifest.get("plannedRuns")
    if declared_planned is not None:
        if not isinstance(declared_planned, list) or declared_planned != planned:
            raise ManifestValidationError(
                "plannedRuns must exactly match the route-derived planned matrix"
            )

    budget = _require_mapping(manifest.get("budget"), "manifest.budget")
    _require_positive_int(budget, "maxCallsPerRun", "manifest.budget")
    _require_positive_int(budget, "maxTotalCalls", "manifest.budget")
    timeouts = _require_mapping(budget.get("timeoutsSeconds"), "manifest.budget.timeoutsSeconds")
    _require_positive_int(timeouts, "step", "manifest.budget.timeoutsSeconds")
    _require_positive_int(timeouts, "run", "manifest.budget.timeoutsSeconds")
    retries = _require_mapping(budget.get("retries"), "manifest.budget.retries")
    for key in ("provider", "structuredOutput"):
        value = retries.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ManifestValidationError(f"manifest.budget.retries.{key} must be non-negative")
    if "maxCostCny" not in budget or budget["maxCostCny"] is not None:
        value = budget.get("maxCostCny")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ManifestValidationError("manifest.budget.maxCostCny must be null or non-negative")

    invalid_rules = manifest.get("infraInvalidRules")
    if not isinstance(invalid_rules, list) or not invalid_rules or any(
        not isinstance(rule, str) or not rule for rule in invalid_rules
    ):
        raise ManifestValidationError("infraInvalidRules must be a non-empty string list")

    pricing = _require_mapping(manifest.get("pricing"), "manifest.pricing")
    for key in ("textInputCnyPerMillion", "textOutputCnyPerMillion", "embeddingCnyPerMillion"):
        value = pricing.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ManifestValidationError(f"manifest.pricing.{key} must be non-negative")
    if pricing.get("currency") != "CNY":
        raise ManifestValidationError("manifest.pricing.currency must be CNY")

    integrity = manifest.get("integrity")
    if integrity is not None:
        integrity_mapping = _require_mapping(integrity, "manifest.integrity")
        sidecar = _require_string(integrity_mapping, "externalSha256File", "manifest.integrity")
        if Path(sidecar).is_absolute():
            raise ManifestValidationError("manifest.integrity.externalSha256File must be relative")
        if integrity_mapping.get("hashScope") != "canonical-json-without-external-digest-field":
            raise ManifestValidationError("manifest.integrity.hashScope is invalid")

    # Return a shallow copy so callers cannot accidentally mutate the object
    # they validated while iterating the plan.
    result = dict(manifest)
    result["plannedRuns"] = planned
    return result


def make_attempt_id(experiment_id: str, route: str, seed: int) -> str:
    """Build a deterministic unique ID for one preregistered route/seed."""

    safe_experiment = re.sub(r"[^A-Za-z0-9_.-]+", "-", experiment_id).strip("-")
    return f"{safe_experiment}:{route}:{seed}"


def planned_attempts(manifest: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Enumerate all planned attempts in stable route/seed order."""

    checked = validate_manifest(manifest)
    return tuple(dict(item) for item in checked["plannedRuns"])


def load_manifest(
    path: str | Path = DEFAULT_MANIFEST_PATH,
    *,
    external_sha256_path: str | Path | None = None,
) -> tuple[dict[str, Any], str]:
    """Load, validate, and externally verify a manifest."""

    manifest_path = Path(path)
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestValidationError(f"cannot load manifest: {manifest_path}") from exc
    if not isinstance(value, dict):
        raise ManifestValidationError("manifest JSON must be an object")
    checked = validate_manifest(value)
    digest = canonical_manifest_sha256(checked)
    sidecar = Path(external_sha256_path) if external_sha256_path is not None else None
    if sidecar is None:
        declared = checked.get("integrity")
        if isinstance(declared, dict):
            sidecar = manifest_path.parent / str(declared["externalSha256File"])
    if sidecar is not None:
        expected = read_external_digest(sidecar)
        if expected != digest:
            raise ManifestValidationError(
                f"manifest digest mismatch: expected {expected}, computed {digest}"
            )
    return checked, digest


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _atomic_write_json(path: Path, value: Any) -> None:
    """Write a record with replace semantics so ``started`` is durable."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """A redacted lifecycle record for one planned attempt."""

    attempt_id: str
    experiment_id: str
    route: str
    seed: int
    strategy_id: str
    status: ManifestStatus
    manifest_digest: str
    planned: bool = True
    started_at: str | None = None
    terminal_at: str | None = None
    run_id: str | None = None
    reason: str | None = None
    infra_valid: bool | None = None
    gameplay_pass: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "attemptId": self.attempt_id,
            "experimentId": self.experiment_id,
            "route": self.route,
            "seed": self.seed,
            "strategyId": self.strategy_id,
            "status": self.status,
            "manifestDigest": self.manifest_digest,
            "planned": self.planned,
            "startedAt": self.started_at,
            "terminalAt": self.terminal_at,
            "runId": self.run_id,
            "reason": self.reason,
            "infraValid": self.infra_valid,
            "gameplayPass": self.gameplay_pass,
        }


class AttemptLedger:
    """Atomically persist per-attempt lifecycle records.

    One file is used per attempt, so concurrent route workers cannot overwrite
    one another's records.  ``prepare`` creates explicit ``not_started`` rows;
    ``start`` replaces the row atomically immediately before the runner can
    call a provider.  This also covers failures before a Run exists.
    """

    def __init__(
        self,
        directory: str | Path,
        *,
        experiment_id: str,
        manifest_digest: str,
        planned: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
    ) -> None:
        if SHA256_RE.fullmatch(manifest_digest) is None:
            raise ValueError("manifest_digest must be a SHA-256")
        self.directory = Path(directory)
        self.experiment_id = experiment_id
        self.manifest_digest = manifest_digest
        self._planned = {str(item["attemptId"]): dict(item) for item in planned}

    def prepare(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        for item in self._planned.values():
            attempt_id = str(item["attemptId"])
            path = self._path(attempt_id)
            if path.exists():
                current = self._read(path)
                self._check_identity(current, item)
                continue
            record = AttemptRecord(
                attempt_id=attempt_id,
                experiment_id=self.experiment_id,
                route=str(item["route"]),
                seed=int(item["seed"]),
                strategy_id=str(item["strategyId"]),
                status="not_started",
                manifest_digest=self.manifest_digest,
            )
            _atomic_write_json(path, record.to_dict())

    def start(self, planned: dict[str, Any] | str) -> dict[str, Any]:
        item = self._planned_item(planned)
        path = self._path(str(item["attemptId"]))
        current = self._read_or_not_started(item, path)
        self._check_identity(current, item)
        status = current.get("status")
        if status == "started":
            raise RuntimeError(f"attempt already started: {item['attemptId']}")
        if status in TERMINAL_ATTEMPT_STATUSES and status != "not_started":
            raise RuntimeError(f"attempt already terminal: {item['attemptId']}")
        updated = dict(current)
        updated.update(
            {
                "status": "started",
                "startedAt": _utc_now(),
                "terminalAt": None,
                "reason": None,
                "runId": None,
                "infraValid": None,
                "gameplayPass": None,
                "manifestDigest": self.manifest_digest,
            }
        )
        _atomic_write_json(path, updated)
        return updated

    def finish(
        self,
        planned: dict[str, Any] | str,
        status: ManifestStatus,
        *,
        run_id: str | None = None,
        reason: str | None = None,
        infra_valid: bool | None = None,
        gameplay_pass: bool | None = None,
    ) -> dict[str, Any]:
        if status not in TERMINAL_ATTEMPT_STATUSES:
            raise ValueError(f"non-terminal attempt status: {status}")
        item = self._planned_item(planned)
        path = self._path(str(item["attemptId"]))
        current = self._read_or_not_started(item, path)
        self._check_identity(current, item)
        existing = current.get("status")
        if existing == "not_started" and current.get("terminalAt") is not None:
            if status == "not_started":
                return current
            raise RuntimeError(f"attempt already terminal: {item['attemptId']}")
        if existing in TERMINAL_ATTEMPT_STATUSES and existing != "not_started":
            if existing != status:
                raise RuntimeError(f"attempt already terminal: {item['attemptId']}")
            return current
        updated = dict(current)
        updated.update(
            {
                "status": status,
                "terminalAt": _utc_now(),
                "runId": run_id,
                "reason": reason,
                "infraValid": infra_valid,
                "gameplayPass": gameplay_pass,
                "manifestDigest": self.manifest_digest,
            }
        )
        _atomic_write_json(path, updated)
        return updated

    def annotate(
        self,
        planned: dict[str, Any] | str,
        *,
        infra_valid: bool | None = None,
        gameplay_pass: bool | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Add post-run quality fields without changing terminal status."""

        item = self._planned_item(planned)
        path = self._path(str(item["attemptId"]))
        current = self._read(path)
        self._check_identity(current, item)
        if current.get("status") not in TERMINAL_ATTEMPT_STATUSES:
            raise RuntimeError(f"attempt is not terminal: {item['attemptId']}")
        updated = dict(current)
        if infra_valid is not None:
            updated["infraValid"] = infra_valid
        if gameplay_pass is not None:
            updated["gameplayPass"] = gameplay_pass
        if run_id is not None:
            updated["runId"] = run_id
        _atomic_write_json(path, updated)
        return updated

    def get(self, attempt_id: str) -> dict[str, Any]:
        return self._read(self._path(attempt_id))

    def records(self) -> list[dict[str, Any]]:
        if not self.directory.exists():
            return []
        values: list[dict[str, Any]] = []
        for path in sorted(self.directory.glob("*.json")):
            values.append(self._read(path))
        return values

    def _planned_item(self, planned: dict[str, Any] | str) -> dict[str, Any]:
        if isinstance(planned, str):
            try:
                return self._planned[planned]
            except KeyError as exc:
                raise KeyError(f"unknown planned attempt: {planned}") from exc
        attempt_id = str(planned.get("attemptId"))
        if self._planned and attempt_id not in self._planned:
            raise KeyError(f"unknown planned attempt: {attempt_id}")
        return dict(planned)

    def _path(self, attempt_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", attempt_id)
        return self.directory / f"{safe}.json"

    def _read_or_not_started(self, item: dict[str, Any], path: Path) -> dict[str, Any]:
        if path.exists():
            return self._read(path)
        record = AttemptRecord(
            attempt_id=str(item["attemptId"]),
            experiment_id=self.experiment_id,
            route=str(item["route"]),
            seed=int(item["seed"]),
            strategy_id=str(item["strategyId"]),
            status="not_started",
            manifest_digest=self.manifest_digest,
        ).to_dict()
        _atomic_write_json(path, record)
        return record

    def _read(self, path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ManifestValidationError(f"invalid attempt record: {path.name}") from exc
        if not isinstance(value, dict):
            raise ManifestValidationError(f"attempt record is not an object: {path.name}")
        status = value.get("status")
        if status not in TERMINAL_ATTEMPT_STATUSES | NON_TERMINAL_ATTEMPT_STATUSES:
            raise ManifestValidationError(f"invalid attempt status: {status}")
        return value

    def _check_identity(self, record: dict[str, Any], item: dict[str, Any]) -> None:
        for key in ("attemptId", "experimentId", "route", "seed", "strategyId"):
            if record.get(key) != item.get(key, self.experiment_id if key == "experimentId" else None):
                raise ManifestValidationError(f"attempt identity mismatch: {key}")
        if record.get("manifestDigest") != self.manifest_digest:
            raise ManifestValidationError("attempt manifest digest mismatch")


__all__ = [
    "ATTEMPT_ID_RE",
    "AttemptLedger",
    "AttemptRecord",
    "AttemptStatus",
    "DEFAULT_MANIFEST_PATH",
    "DEFAULT_MANIFEST_SCHEMA_PATH",
    "DEFAULT_MANIFEST_SHA256_PATH",
    "DEFAULT_PREREG_SEEDS",
    "EXPECTED_MANIFEST_ROUTES",
    "ManifestStatus",
    "ManifestValidationError",
    "NON_TERMINAL_ATTEMPT_STATUSES",
    "TERMINAL_ATTEMPT_STATUSES",
    "canonical_json_bytes",
    "canonical_manifest_sha256",
    "file_sha256",
    "load_manifest",
    "make_attempt_id",
    "planned_attempts",
    "read_external_digest",
    "validate_manifest",
]
