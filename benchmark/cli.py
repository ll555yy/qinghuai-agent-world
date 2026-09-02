from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from benchmark.common.ark_budget import AFPBudgetGuard, ArkAFPClient, BudgetExhausted
from benchmark.common.artifacts import ArtifactStore, sha256_file
from benchmark.common.models import BudgetPolicy, ExperimentManifest, ResumeMetrics
from benchmark.common.reporting import markdown_report

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=False)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
RESULTS_ROOT = ROOT / "benchmark" / "results"
SUITES = ("business", "memory", "reliability")


def _tree_digest(paths: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.as_posix()):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _scenario_digest() -> str:
    return _tree_digest(tuple((ROOT / "core" / "scenario").glob("*.yaml")))


def _prompt_digest() -> str:
    return _tree_digest(
        (
            ROOT / "core" / "backend" / "app" / "agents" / "runtime.py",
            ROOT / "core" / "backend" / "app" / "orchestration" / "run_service.py",
            ROOT / "core" / "backend" / "app" / "ai" / "decision_service.py",
        )
    )


def _dataset_path(suite: str) -> Path | None:
    return {
        "business": ROOT / "benchmark" / "business" / "tasks.yaml",
        "memory": ROOT / "benchmark" / "memory" / "dataset_v1.yaml",
        "reliability": ROOT / "benchmark" / "reliability" / "faults.yaml",
    }.get(suite)


def _experiment_id(suite: str, mode: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"p0-{suite}-{mode}-{stamp}"


def validate() -> dict[str, Any]:
    from benchmark.business import load_tasks
    from benchmark.memory import load_memory_dataset
    from benchmark.reliability import load_fault_plans

    tasks = load_tasks()
    memory = load_memory_dataset()
    faults = load_fault_plans()
    result = {
        "valid": True,
        "businessTasks": len(tasks),
        "businessRoutes": 2,
        "memoryQueries": len(memory.queries),
        "memoryTuning": sum(case.split == "tuning" for case in memory.queries),
        "memoryHoldout": sum(case.split == "holdout" for case in memory.queries),
        "faultFamilies": len(faults),
        "faultAttempts": sum(plan.attempts for plan in faults),
    }
    if result != {
        **result,
        "businessTasks": 12,
        "businessRoutes": 2,
        "memoryQueries": 100,
        "memoryTuning": 30,
        "memoryHoldout": 70,
        "faultFamilies": 8,
        "faultAttempts": 80,
    }:
        raise RuntimeError(f"benchmark completion contract failed: {result}")
    return result


class _OracleRetriever:
    """Deterministic pipeline smoke fixture, explicitly excluded from live evidence."""

    async def search(self, case: Any, config: Any) -> Mapping[str, Any]:
        expected = tuple(case.expected_memory_ids)
        if config.config_id == "R1_keyword_only" and case.subset in {"semantic_paraphrase", "graph_only"} or config.config_id == "R2_vector_only" and case.subset in {"topic_alias", "actor_goal_filter", "graph_only"} or config.config_id == "R3_no_graph" and case.subset == "graph_only":
            expected = ()
        elif config.config_id == "R4_no_owner_guard":
            expected = tuple(case.distractor_memory_ids or expected)
        return {"memoryIds": expected, "latencyMs": 0.1}


def _live_preflight(suite: str, *, manual_afp: bool = False) -> AFPBudgetGuard | None:
    required: list[str] = []
    if suite == "business":
        required.extend(("ARK_API_KEY", "ARK_MODEL"))
    if not manual_afp and suite != "reliability":
        required.extend(("ARK_AFP_ACCESS_KEY_ID", "ARK_AFP_SECRET_ACCESS_KEY"))
    if suite == "memory":
        required.append("ARK_EMBEDDING_MODEL")
    if suite in {"memory", "reliability"}:
        required.append("QINGHUAI_TEST_DATABASE_URL")
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    if missing:
        raise RuntimeError(f"live preflight missing: {', '.join(missing)}")
    test_url = os.environ.get("QINGHUAI_TEST_DATABASE_URL", "").strip()
    if test_url and test_url in {
        os.environ.get("DATABASE_URL", "").strip(),
        os.environ.get("QINGHUAI_DATABASE_URL", "").strip(),
    }:
        raise RuntimeError("QINGHUAI_TEST_DATABASE_URL must not equal a development database URL")
    if manual_afp or suite == "reliability":
        return None
    guard = AFPBudgetGuard(BudgetPolicy(), ArkAFPClient().get_usage)
    guard.start()
    return guard


def _manifest(
    suite: str,
    mode: str,
    seeds: Sequence[int],
    experiment_id: str,
    *,
    manual_afp: bool = False,
) -> ExperimentManifest:
    dataset_path = _dataset_path(suite)
    return ExperimentManifest(
        experiment_id=experiment_id,
        suite=suite,
        execution_mode=mode,
        seeds=tuple(int(seed) for seed in seeds),
        scenario_digest=_scenario_digest(),
        prompt_digest=_prompt_digest(),
        dataset_digest=sha256_file(dataset_path) if dataset_path else None,
        candidate_model=os.environ.get("ARK_MODEL") if mode == "live" else None,
        embedding_model=os.environ.get("ARK_EMBEDDING_MODEL") if mode == "live" else None,
        temperature=0.1 if mode == "live" else None,
        preregistered_thresholds={
            "fullVsRandomDeltaPp": 30.0,
            "fullVsMyopicDeltaPp": 15.0,
            "paraphraseRecallDeltaPp": 10.0,
            "graphRecallDeltaPp": 15.0,
            "ownerViolations": 0.0,
            "faultRecoveryRate": 1.0,
        },
        budget=BudgetPolicy(require_live_meter=not manual_afp),
    )


def _business_aggregate(results: Sequence[Any]) -> dict[str, Any]:
    from benchmark.business import aggregate_business_results

    aggregate = aggregate_business_results(results)
    conditions = aggregate["conditions"]
    random_delta = aggregate["pairedEffects"]["fullVsRandom"]["deltaPp"]
    myopic_delta = aggregate["pairedEffects"]["fullVsMyopic"]["deltaPp"]
    aggregate.update(
        {
            "sampleSize": aggregate["sampleCount"],
            "taskSuccessRate": conditions.get("A0_full", {}).get("taskSuccessRate"),
            "infraValidRate": 1
            - sum(item["infraInvalid"] for item in conditions.values()) / aggregate["sampleCount"],
            "effectSize": random_delta,
            "confidenceInterval95": list(
                aggregate["pairedEffects"]["fullVsRandom"]["confidenceInterval95"].values()
            ),
            "hypothesisVerified": bool(
                random_delta is not None
                and myopic_delta is not None
                and random_delta >= 30
                and myopic_delta >= 15
            ),
            "baselines": [
                {"id": key, "taskSuccessRate": value.get("taskSuccessRate")}
                for key, value in conditions.items()
                if key.startswith("B")
            ],
        }
    )
    return aggregate


async def _run_business(
    seeds: Sequence[int],
    live: bool,
    guard: AFPBudgetGuard | None,
    *,
    emit: Any | None = None,
    completed: set[tuple[Any, ...]] | None = None,
    existing_rows: Sequence[Mapping[str, Any]] = (),
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    from benchmark.business import BusinessTaskRunner
    from benchmark.business.runner import POLICY_IDS, ROUTES

    decider = None
    limitations = ["offline A0 uses a frozen plan and is not live-model evidence"]
    if live:
        from benchmark.integrations.ark import ArkBusinessDecider
        from core.backend.app.ai.ark_client import ArkClient

        decider = ArkBusinessDecider(ArkClient(), budget_guard=guard)
        limitations = [
            "live A0 uses the real Ark candidate over the frozen public task state",
            "business task effects are a benchmark environment, not a production RunService replay",
        ]
    runner = BusinessTaskRunner()
    results = []
    completed = completed or set()
    for task in runner.tasks:
        for route in ROUTES:
            for seed in seeds:
                paired_id = f"{task.task_id}:{route}:seed-{seed}"
                for condition_id in POLICY_IDS:
                    key = (task.task_id, condition_id, route, int(seed))
                    if key in completed:
                        continue
                    result = await runner.run_task(
                        task.task_id,
                        condition_id,
                        seed=int(seed),
                        route=route,
                        paired_group_id=paired_id,
                        decider=decider,
                    )
                    results.append(result)
                    if emit is not None:
                        emit(result.as_dict())
    rows = [dict(row) for row in existing_rows] + [result.as_dict() for result in results]
    return rows, _business_aggregate([*existing_rows, *results]), limitations


async def _run_memory(
    live: bool,
    *,
    emit: Any | None = None,
    completed: set[tuple[Any, ...]] | None = None,
    existing_rows: Sequence[Mapping[str, Any]] = (),
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    from benchmark.memory import (
        DEFAULT_CONFIGS,
        load_memory_dataset,
        run_memory_benchmark,
    )
    from benchmark.memory.scorer import aggregate_observations, compute_paired_effects

    dataset = load_memory_dataset()
    completed = completed or set()
    reports: list[dict[str, Any]] = []

    async def execute_with(retriever: Any) -> None:
        for config in DEFAULT_CONFIGS.values():
            missing = [
                case for case in dataset.queries
                if (case.case_id, config.config_id) not in completed
            ]
            if not missing:
                continue
            reports.append(
                await run_memory_benchmark(
                    retriever,
                    cases=missing,
                    configs=(config,),
                    trace_sink=emit,
                )
            )

    if live:
        from benchmark.integrations.postgres_memory import PostgresMemoryHarness
        from core.backend.app.ai.ark_embedding import (
            ArkEmbeddingClient,
            ArkEmbeddingSettings,
        )

        embedding = ArkEmbeddingClient(
            ArkEmbeddingSettings(model=os.environ["ARK_EMBEDDING_MODEL"])
        )
        async with PostgresMemoryHarness(
            os.environ["QINGHUAI_TEST_DATABASE_URL"], dataset, embedding
        ) as harness:
            await execute_with(harness)
        limitations = ["R4 is a benchmark-local unsafe negative control and is excluded from production configuration"]
    else:
        await execute_with(_OracleRetriever())
        limitations = ["oracle retrieval is a deterministic scorer/pipeline smoke test, not retrieval quality evidence"]
    observations = [dict(row) for row in existing_rows] + [
        row for report in reports for row in report["observations"]
    ]
    aggregate = aggregate_observations(observations)
    effects = compute_paired_effects(observations)
    full_holdout = [
        float(item["metrics"]["recall_at_5"])
        for item in observations
        if item.get("configId") == "R0_full_hybrid"
        and item.get("split") == "holdout"
        and isinstance(item.get("metrics"), Mapping)
        and item["metrics"].get("recall_at_5") is not None
    ]
    aggregate.update(
        {
            "sampleSize": len(observations),
            "taskSuccessRate": None,
            "recallAt5": sum(full_holdout) / len(full_holdout) if full_holdout else None,
            "infraValidRate": sum(item.get("error") is None for item in observations) / len(observations),
            "pairedEffects": effects,
            "hypothesisVerified": None if not live else _memory_hypothesis(effects, aggregate),
        }
    )
    return observations, aggregate, limitations


def _memory_hypothesis(effects: Mapping[str, Any], aggregate: Mapping[str, Any]) -> bool:
    semantic = effects.get("semantic_paraphrase", {})
    graph = effects.get("graph_only", {})
    full_metrics = (
        aggregate.get("by_config", {})
        .get("R0_full_hybrid", {})
        .get("aggregate", {})
    )
    return (
        semantic.get("status") == "ok"
        and graph.get("status") == "ok"
        and float(semantic.get("mean_difference") or 0) >= 0.10
        and float(graph.get("mean_difference") or 0) >= 0.15
        and int(full_metrics.get("ownerBoundaryViolations", 0) or 0) == 0
    )


async def _run_reliability(
    seeds: Sequence[int],
    live: bool,
    *,
    emit: Any | None = None,
    completed: set[tuple[Any, ...]] | None = None,
    existing_rows: Sequence[Mapping[str, Any]] = (),
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    if live:
        from benchmark.integrations.production_reliability import (
            ProductionReliabilityAdapter,
        )
        from benchmark.reliability.runner import ReliabilityRunner

        async with ProductionReliabilityAdapter(
            os.environ["QINGHUAI_TEST_DATABASE_URL"]
        ) as adapter:
            results, _summary = await ReliabilityRunner(
                adapter, execution_mode="production_postgres"
            ).run_async(seeds=seeds)
        limitations = [
            "faults are deterministic local boundary injections over production RunService and PostgreSQL",
            "F4 injects a disconnect immediately before repository transaction entry; it is not a physical network outage",
            "provider-originated incidents are reported separately from the local recovery rate",
        ]
    else:
        from benchmark.reliability import run_reliability_benchmark

        results, _summary = run_reliability_benchmark(seeds=seeds)
        limitations = ["default reliability adapter is deterministic; production adapters must be source-labelled separately"]
    completed = completed or set()
    rows = [dict(row) for row in existing_rows] + [
        result.to_dict()
        for result in results
        if (result.fault_id, int(result.seed)) not in completed
    ]
    if emit is not None:
        for row in rows[len(existing_rows):]:
            emit(row)
    total = len(rows)
    recovery = sum(bool(row.get("recovered")) for row in rows)
    divergent = sum(bool(row.get("stateDiverged")) for row in rows)
    duplicate = sum(bool(row.get("duplicateSideEffect")) for row in rows)
    infra_invalid = sum(not bool(row.get("infraValid")) for row in rows)
    retry_rows = [row for row in rows if int(row.get("retryCount", 0)) > 0]
    recovery_times = [float(row["recoveryTimeMs"]) for row in rows if row.get("recoveryTimeMs") is not None]
    from benchmark.common.statistics import percentile

    aggregate = {
        "totalAttempts": total,
        "recoverySuccesses": recovery,
        "recoverySuccessRate": recovery / total if total else 0,
        "stateDivergences": divergent,
        "stateDivergenceRate": divergent / total if total else 0,
        "duplicateSideEffects": duplicate,
        "duplicateSideEffectRate": duplicate / total if total else 0,
        "retryAttempts": len(retry_rows),
        "retrySuccesses": sum(bool(row.get("retrySucceeded")) for row in retry_rows),
        "infraInvalid": infra_invalid,
        "infraInvalidRate": infra_invalid / total if total else 0,
        "recoveryTimeP50Ms": percentile(recovery_times, 50),
        "recoveryTimeP95Ms": percentile(recovery_times, 95),
    }
    aggregate.update(
        {
            "sampleSize": aggregate["totalAttempts"],
            "taskSuccessRate": aggregate["recoverySuccessRate"],
            "infraValidRate": 1 - aggregate["infraInvalidRate"],
            "p95LatencyMs": aggregate["recoveryTimeP95Ms"],
            "hypothesisVerified": (
                None
                if not live
                else recovery == total
                and divergent == 0
                and duplicate == 0
                and infra_invalid == 0
            ),
        }
    )
    return rows, aggregate, limitations


def _resume_metrics(experiment_id: str, suite: str, aggregate: Mapping[str, Any], limitations: Sequence[str]) -> ResumeMetrics:
    primary_names = {
        "memory": "recallAt5",
        "reliability": "recoverySuccessRate",
    }
    primary_name = primary_names.get(suite, "taskSuccessRate")
    return ResumeMetrics(
        experiment_id=experiment_id,
        sample_size=int(aggregate.get("sampleSize", 0)),
        primary_metric_name=primary_name,
        primary_metric_value=aggregate.get(primary_name),
        primary_metric_unit="ratio",
        baselines=tuple(aggregate.get("baselines", ())),
        effect_size=aggregate.get("effectSize"),
        confidence_interval_95=(tuple(aggregate["confidenceInterval95"]) if aggregate.get("confidenceInterval95") else None),
        afp_per_success=aggregate.get("afpPerSuccess"),
        cost_per_success_cny_estimated=aggregate.get("costPerSuccessCnyEstimated"),
        p95_latency_ms=aggregate.get("p95LatencyMs"),
        limitations=tuple(limitations),
        hypothesis_verified=aggregate.get("hypothesisVerified"),
    )


def _attempt_rows(directory: Path) -> list[dict[str, Any]]:
    path = directory / "attempts.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _completed_keys(suite: str, rows: Sequence[Mapping[str, Any]]) -> set[tuple[Any, ...]]:
    if suite == "business":
        return {
            (row.get("taskId"), row.get("conditionId"), row.get("route"), int(row.get("seed", 0)))
            for row in rows
        }
    if suite == "memory":
        return {(row.get("caseId"), row.get("configId")) for row in rows}
    return {(row.get("faultId"), int(row.get("seed", 0))) for row in rows}


async def execute(
    suite: str,
    *,
    mode: str,
    seeds: Sequence[int],
    experiment_id: str | None = None,
    manifest_override: ExperimentManifest | None = None,
    manual_afp: bool = False,
) -> list[Path]:
    selected = SUITES if suite == "all" else (suite,)
    outputs: list[Path] = []
    for current in selected:
        current_id = experiment_id or _experiment_id(current, mode)
        if len(selected) > 1:
            current_id = f"{current_id}-{current}"
        live = mode == "live"
        if manifest_override is not None:
            manual_afp = not manifest_override.budget.require_live_meter
        guard = _live_preflight(current, manual_afp=manual_afp) if live else None
        manifest = manifest_override or _manifest(
            current,
            mode,
            seeds,
            current_id,
            manual_afp=manual_afp,
        )
        store = ArtifactStore(RESULTS_ROOT, current_id)
        directory = store.initialize(manifest)
        if live and manual_afp and current != "reliability":
            store.append_jsonl(
                "budget-ledger.jsonl",
                {
                    "status": "manual_afp_monitoring",
                    "automaticStop": False,
                    "afpMetricsAvailable": False,
                },
            )
        existing_rows = _attempt_rows(directory)
        completed = _completed_keys(current, existing_rows)
        def emit(row: Mapping[str, Any], artifact_store: ArtifactStore = store) -> None:
            artifact_store.append_jsonl("attempts.jsonl", row)
        try:
            if current == "business":
                rows, aggregate, limitations = await _run_business(
                    seeds,
                    live,
                    guard,
                    emit=emit,
                    completed=completed,
                    existing_rows=existing_rows,
                )
            elif current == "memory":
                rows, aggregate, limitations = await _run_memory(
                    live,
                    emit=emit,
                    completed=completed,
                    existing_rows=existing_rows,
                )
            else:
                rows, aggregate, limitations = await _run_reliability(
                    seeds,
                    live,
                    emit=emit,
                    completed=completed,
                    existing_rows=existing_rows,
                )
        except BudgetExhausted as exc:
            store.append_jsonl(
                "budget-ledger.jsonl",
                {"status": "budget_exhausted", "message": str(exc), "usage": exc.usage.as_dict() if exc.usage else None},
            )
            raise
        if live and manual_afp and current != "reliability":
            limitations = [
                *limitations,
                "AFP was monitored manually; automatic quota stop and afpPerSuccess are unavailable",
            ]
        resume = _resume_metrics(current_id, current, aggregate, limitations)
        failures = [
            row
            for row in rows
            if row.get("failureType")
            or row.get("error")
            or row.get("failureCode")
            or row.get("infraValid") is False
            or row.get("status") == "infra_invalid"
        ]
        store.write_report(
            per_cases=rows,
            aggregate=aggregate,
            report_markdown=markdown_report(current_id, current, aggregate),
            failure_markdown=(
                "# Failure analysis\n\n"
                f"Retained failures: {len(failures)}/{len(rows)}. See `per-case.jsonl` and `raw-traces/`.\n"
            ),
            resume_metrics=resume,
            readme=(
                f"# {current_id}\n\nExecution mode: `{mode}`.\n\n"
                + "\n".join(f"- {item}" for item in limitations)
            ),
        )
        outputs.append(directory)
    return outputs


def _read_report(experiment_id: str) -> dict[str, Any]:
    directory = (RESULTS_ROOT / experiment_id).resolve()
    if RESULTS_ROOT.resolve() not in directory.parents or not directory.is_dir():
        raise FileNotFoundError(f"unknown experiment: {experiment_id}")
    return {
        "experimentId": experiment_id,
        "manifest": json.loads((directory / "manifest.json").read_text(encoding="utf-8")),
        "aggregate": json.loads((directory / "aggregate.json").read_text(encoding="utf-8")),
        "resumeMetrics": json.loads((directory / "resume-metrics.json").read_text(encoding="utf-8")),
        "directory": str(directory),
    }


def _manifest_from_mapping(value: Mapping[str, Any]) -> ExperimentManifest:
    budget_value = value.get("budget", {})
    budget = BudgetPolicy(**budget_value) if isinstance(budget_value, Mapping) else BudgetPolicy()
    return ExperimentManifest(
        experiment_id=str(value["experiment_id"]),
        suite=str(value["suite"]),
        execution_mode=str(value["execution_mode"]),
        seeds=tuple(int(seed) for seed in value["seeds"]),
        scenario_digest=str(value["scenario_digest"]),
        prompt_digest=str(value["prompt_digest"]),
        dataset_digest=value.get("dataset_digest"),
        candidate_model=value.get("candidate_model"),
        embedding_model=value.get("embedding_model"),
        temperature=value.get("temperature"),
        preregistered_thresholds=dict(value.get("preregistered_thresholds", {})),
        budget=budget,
        schema_version=str(value.get("schema_version", "1.0")),
        created_at=str(value["created_at"]),
    )


def _expected_attempts(manifest: ExperimentManifest) -> int:
    multiplier = {
        "business": 12 * 2 * 4,
        "memory": 100 * 5,
        "reliability": 8,
    }[manifest.suite]
    return multiplier * len(manifest.seeds)


async def resume_experiment(experiment_id: str) -> dict[str, Any]:
    directory = (RESULTS_ROOT / experiment_id).resolve()
    if RESULTS_ROOT.resolve() not in directory.parents or not directory.is_dir():
        raise FileNotFoundError(f"unknown experiment: {experiment_id}")
    manifest = _manifest_from_mapping(
        json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    )
    current = len(_attempt_rows(directory))
    expected = _expected_attempts(manifest)
    if current < expected:
        await execute(
            manifest.suite,
            mode=manifest.execution_mode,
            seeds=manifest.seeds,
            experiment_id=manifest.experiment_id,
            manifest_override=manifest,
        )
        current = len(_attempt_rows(directory))
    report = _read_report(experiment_id)
    return {
        "status": "complete" if current >= expected else "partial",
        "attempts": current,
        "expectedAttempts": expected,
        "experimentId": experiment_id,
        "directory": report["directory"],
        "resumeMetrics": report["resumeMetrics"],
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Qinghuai P0 benchmark")
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    for name in ("pilot", "run"):
        command = sub.add_parser(name)
        command.add_argument("--suite", choices=("all", *SUITES), default="all")
        command.add_argument("--live", action="store_true")
        command.add_argument(
            "--manual-afp",
            action="store_true",
            help="run live without GetAFPUsage; operator is responsible for stopping",
        )
        command.add_argument("--experiment-id")
        command.add_argument("--seeds", default=None, help="comma-separated frozen integer seeds")
    for name in ("resume", "report"):
        command = sub.add_parser(name)
        command.add_argument("--experiment-id", required=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "validate":
        print(json.dumps(validate(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "report":
        print(json.dumps(_read_report(args.experiment_id), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "resume":
        report = asyncio.run(resume_experiment(args.experiment_id))
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.seeds:
        seeds = tuple(int(item.strip()) for item in args.seeds.split(",") if item.strip())
    else:
        seeds = (0, 1, 2) if args.command == "pilot" else tuple(range(10))
    mode = "live" if args.live else ("pilot" if args.command == "pilot" else "offline")
    if args.manual_afp and not args.live:
        raise SystemExit("--manual-afp requires --live")
    outputs = asyncio.run(
        execute(
            args.suite,
            mode=mode,
            seeds=seeds,
            experiment_id=args.experiment_id,
            manual_afp=args.manual_afp,
        )
    )
    print(json.dumps({"status": "completed", "outputs": [str(path) for path in outputs]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
