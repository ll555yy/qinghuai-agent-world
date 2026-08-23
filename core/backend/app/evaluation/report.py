"""Stable, privacy-preserving report projections for semantic evaluations.

Only this module decides what leaves the evaluator.  Provider objects,
requests, complete candidate traces, and case input contexts are never copied
to a report.  Public helpers accept dictionaries as well as the Pydantic and
dataclass result objects used by the evaluation contracts.
"""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, cast

from .models import EvaluationReport

_SECRET_KEY = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|authorization|database[_-]?url|password|secret|coreSecrets?)"
)
_URL = re.compile(r"(?i)(?:postgres(?:ql)?|https?|mysql)://[^\s\"']+")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_KEY_ASSIGNMENT = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|authorization|password|secret)\s*[:=]\s*[^,;\s]+"
)
_LONG_TOKEN = re.compile(r"\b(?:sk|ark|token)[_-][A-Za-z0-9_-]{12,}\b", re.IGNORECASE)
_INTERNAL_FIELD_ASSIGNMENT = re.compile(
    r"(?ix)"
    r"[\"']?\b(?:owner[_-]?npc[_-]?id|trace[_-]?id|core[_-]?secrets?|"
    r"private[_-]?memory|system[_-]?prompt|full[_-]?candidate[_-]?output)\b"
    r"[\"']?(?:\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;|}\]]+))?"
)
_PRIVATE_FIELD_NAMES = {
    "system_prompt",
    "systemPrompt",
    "prompt",
    "messages",
    "core_secrets",
    "coreSecrets",
    "api_key",
    "apiKey",
    "database_url",
    "databaseUrl",
    "trace",
    "raw_trace",
    "rawTrace",
    "private_memory",
    "privateMemory",
    "full_candidate_output",
    "fullCandidateOutput",
    "owner_npc_id",
    "ownerNpcId",
    "trace_id",
    "traceId",
}
_SAFE_RESULT_KEYS = {
    "caseId",
    "case_id",
    "caseVersion",
    "category",
    "protocol",
    "status",
    "runs",
    "runIndex",
    "ruleScore",
    "ruleScores",
    "judgeScore",
    "judgeScores",
    "judgeDisagreement",
    "judgeEffectiveConfidence",
    "reviewReasons",
    "candidateSummary",
    "errorCode",
    "required",
    "skipped",
}


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _plain(model_dump(mode="json"))
        except TypeError:
            return _plain(model_dump())
    if is_dataclass(value):
        return _plain(asdict(cast(Any, value)))
    if hasattr(value, "value") and isinstance(value.value, (str, int, float, bool)):
        return value.value
    return str(value)


def _redact_text(value: str, forbidden: Iterable[str] = ()) -> str:
    output = value
    # Exact forbidden canaries are replaced first.  Longer values first avoids
    # a short marker partially matching a longer marker.
    signals = sorted(
        {str(item) for item in forbidden if isinstance(item, str) and item},
        key=len,
        reverse=True,
    )
    for signal in signals:
        output = output.replace(signal, "[REDACTED]")
    # Candidate summaries are untrusted text, not a trusted JSON object.  A
    # model can therefore leak internal field names even when it does not
    # include an API key or a canary.  Remove both the marker and an adjacent
    # scalar value (including JSON-style quoted values).
    output = _INTERNAL_FIELD_ASSIGNMENT.sub("[REDACTED]", output)
    output = _URL.sub("[REDACTED]", output)
    output = _BEARER.sub("[REDACTED]", output)
    output = _KEY_ASSIGNMENT.sub("[REDACTED]", output)
    output = _LONG_TOKEN.sub("[REDACTED]", output)
    return output


def redact(value: Any, *, forbidden: Iterable[str] = (), max_text: int | None = None) -> Any:
    """Recursively redact secrets and case canaries from arbitrary values."""

    forbidden_tuple = tuple(forbidden)
    if isinstance(value, str):
        output = _redact_text(value, forbidden_tuple)
        if max_text is not None and len(output) > max_text:
            output = output[: max(0, max_text - 1)] + "…"
        return output
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, raw_item in value.items():
            key = str(raw_key)
            if key in _PRIVATE_FIELD_NAMES or _SECRET_KEY.search(key):
                continue
            result[key] = redact(raw_item, forbidden=forbidden_tuple, max_text=max_text)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact(item, forbidden=forbidden_tuple, max_text=max_text) for item in value]
    return _plain(value)


def _case_forbidden(case: Any) -> list[str]:
    if isinstance(case, Mapping):
        value = case.get("forbidden_signals", case.get("forbiddenSignals", []))
    else:
        value = getattr(case, "forbidden_signals", getattr(case, "forbiddenSignals", []))
    return [str(item) for item in value] if isinstance(value, (list, tuple, set)) else []


def _case_value(case: Any, *names: str, default: Any = None) -> Any:
    if isinstance(case, Mapping):
        for name in names:
            if name in case:
                return case[name]
    else:
        for name in names:
            if hasattr(case, name):
                return getattr(case, name)
    return default


def _safe_case_result(item: Any, case_by_id: Mapping[str, Any]) -> dict[str, Any]:
    plain = _plain(item)
    if not isinstance(plain, Mapping):
        plain = {"caseId": "", "status": "invalid", "reviewReasons": ["invalid_case_result"]}
    case_id = str(plain.get("caseId", plain.get("case_id", "")))
    forbidden = _case_forbidden(case_by_id.get(case_id, {}))
    safe: dict[str, Any] = {}
    for raw_key, raw_value in plain.items():
        key = str(raw_key)
        if key in _PRIVATE_FIELD_NAMES or _SECRET_KEY.search(key):
            continue
        if key not in _SAFE_RESULT_KEYS and key not in {"candidateOutput", "observation"}:
            # Keep unknown rule/judge fields only when they are simple safe
            # metrics.  Complete observations are intentionally excluded.
            if key not in {"estimatedCostCny", "hardFailure", "hard_failure", "failures"}:
                continue
        if key in {"candidateOutput", "observation"}:
            continue
        if key == "candidateSummary":
            safe[key] = redact(str(raw_value), forbidden=forbidden, max_text=240)
        elif key == "runs" and isinstance(raw_value, list):
            runs: list[dict[str, Any]] = []
            for run in raw_value:
                if not isinstance(run, Mapping):
                    continue
                run_safe: dict[str, Any] = {}
                for run_key, run_value in run.items():
                    if (
                        run_key
                        in {
                            "observation",
                            "candidateOutput",
                            "fullCandidateOutput",
                            "request",
                        }
                        or str(run_key) in _PRIVATE_FIELD_NAMES
                        or _SECRET_KEY.search(str(run_key))
                    ):
                        continue
                    if run_key == "candidateSummary":
                        run_safe[run_key] = redact(str(run_value), forbidden=forbidden, max_text=240)
                    else:
                        run_safe[str(run_key)] = redact(run_value, forbidden=forbidden, max_text=240)
                runs.append(run_safe)
            safe[key] = runs
        else:
            safe[key] = redact(raw_value, forbidden=forbidden, max_text=240)
    safe.setdefault("caseId", case_id)
    safe.setdefault("reviewReasons", [])
    if isinstance(safe["reviewReasons"], list):
        safe["reviewReasons"] = sorted({str(item) for item in safe["reviewReasons"]})
    return safe


def _score_value(score: Any, *names: str, default: Any = None) -> Any:
    if isinstance(score, Mapping):
        for name in names:
            if name in score:
                return score[name]
    else:
        for name in names:
            if hasattr(score, name):
                return getattr(score, name)
    return default


def _percentile(values: Iterable[float], percentile: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * percentile + 0.999999)))
    return round(ordered[index], 6)


def _rate(values: Iterable[bool]) -> float | None:
    items = list(values)
    return round(sum(items) / len(items), 6) if items else None


def _rule_entries(cases: list[dict[str, Any]]) -> list[tuple[dict[str, Any], Mapping[str, Any], int]]:
    entries: list[tuple[dict[str, Any], Mapping[str, Any], int]] = []
    for case in cases:
        runs = case.get("runs", [])
        if isinstance(runs, list):
            for position, run in enumerate(runs):
                if not isinstance(run, Mapping):
                    continue
                value = run.get("ruleScore")
                if isinstance(value, Mapping):
                    entries.append((case, value, int(run.get("runIndex", position))))
        if not runs:
            value = case.get("ruleScore")
            if isinstance(value, Mapping):
                entries.append((case, value, 0))
    return entries


def _rule_metrics(cases: list[dict[str, Any]]) -> dict[str, Any]:
    entries = _rule_entries(cases)
    scores = [score for _, score, _ in entries]
    hard = sum(
        1
        for score in scores
        if bool(_score_value(score, "hard_failure", "hardFailure", default=False))
    )
    failures: dict[str, int] = {}
    for score in scores:
        values = _score_value(score, "failures", default=[])
        if isinstance(values, list):
            for value in values:
                text = str(value)
                failures[text] = failures.get(text, 0) + 1
    retrieval_scores = [
        score for case, score, _ in entries if case.get("protocol") == "memory_retrieval"
    ]

    def numbers(items: Iterable[Mapping[str, Any]], name: str) -> list[float]:
        return [
            float(value)
            for item in items
            if isinstance((value := _score_value(item, name, _camel(name), default=None)), (int, float))
            and not isinstance(value, bool)
        ]

    def blocked_rate(count_name: str) -> float | None:
        invalid = [
            score
            for score in scores
            if int(_score_value(score, count_name, _camel(count_name), default=0) or 0) > 0
        ]
        known = [
            score
            for score in invalid
            if _score_value(score, "system_blocked", "systemBlocked", default=None)
            is not None
        ]
        return (
            _rate(
                bool(_score_value(score, "system_blocked", "systemBlocked", default=False))
                for score in known
            )
            if known
            else None
        )

    schema = [
        bool(
            _score_value(
                score,
                "protocol_schema_valid",
                "protocolSchemaValid",
                "schema_valid",
                "schemaValid",
                default=False,
            )
        )
        for score in scores
    ]
    first_schema = [
        bool(
            _score_value(
                score,
                "protocol_schema_valid",
                "protocolSchemaValid",
                "schema_valid",
                "schemaValid",
                default=False,
            )
        )
        for _, score, run_index in entries
        if run_index == 0
    ]
    case_constraints = [
        bool(
            _score_value(
                score,
                "case_constraint_valid",
                "caseConstraintValid",
                default=False,
            )
        )
        for score in scores
    ]
    candidate_violations = [
        score
        for score in scores
        if bool(
            _score_value(
                score,
                "candidate_violation",
                "candidateViolation",
                default=False,
            )
        )
    ]
    known_system_results = [
        score
        for score in candidate_violations
        if _score_value(score, "system_blocked", "systemBlocked", default=None)
        is not None
    ]
    direct = [
        bool(value)
        for score in scores
        if (value := _score_value(score, "direct_question_pass", "directQuestionPass", default=None))
        is not None
    ]
    latencies = numbers(scores, "latency_ms")
    prompt_tokens = sum(int(value) for value in numbers(scores, "prompt_tokens"))
    completion_tokens = sum(int(value) for value in numbers(scores, "completion_tokens"))
    total_tokens = sum(int(value) for value in numbers(scores, "total_tokens"))
    return {
        "casesScored": len(scores),
        "schemaSuccessRate": _rate(schema),
        "firstAttemptSchemaSuccessRate": _rate(first_schema),
        "protocolSchemaSuccessRate": _rate(schema),
        "caseConstraintSuccessRate": _rate(case_constraints),
        "candidateViolations": len(candidate_violations),
        "candidateViolationRate": _rate(score in candidate_violations for score in scores),
        "systemBlockedRate": (
            _rate(
                bool(_score_value(score, "system_blocked", "systemBlocked", default=False))
                for score in known_system_results
            )
            if known_system_results
            else None
        ),
        "endToEndSafetyFailures": sum(
            _score_value(
                score,
                "end_to_end_safety_failure",
                "endToEndSafetyFailure",
                default=False,
            )
            is True
            for score in scores
        ),
        "invalidActionBlockedRate": blocked_rate("invalid_action_count"),
        "invalidIdBlockedRate": blocked_rate("invalid_id_count"),
        "invalidEvidenceBlockedRate": blocked_rate("invalid_evidence_count"),
        "queryScopePassRate": _rate(
            bool(_score_value(score, "query_scope_valid", "queryScopeValid", default=True))
            for score in scores
        ),
        "retrievalScopePassRate": _rate(
            bool(
                _score_value(
                    score,
                    "retrieval_scope_valid",
                    "retrievalScopeValid",
                    default=True,
                )
            )
            for score in scores
        ),
        "committedEvidenceScopePassRate": _rate(
            bool(
                _score_value(
                    score,
                    "committed_evidence_scope_valid",
                    "committedEvidenceScopeValid",
                    default=True,
                )
            )
            for score in scores
        ),
        "ownerLeakCount": sum(int(value) for value in numbers(scores, "owner_leak_count")),
        "canaryLeakCount": sum(int(value) for value in numbers(scores, "canary_leak_count")),
        "forbiddenSignalCount": sum(
            int(value) for value in numbers(scores, "forbidden_signal_count")
        ),
        "internalFieldLeakCount": sum(
            int(value) for value in numbers(scores, "internal_field_leak_count")
        ),
        "memoryToolCallLimitPassRate": _rate(
            bool(
                _score_value(
                    score,
                    "memory_tool_limit_valid",
                    "memoryToolLimitValid",
                    default=True,
                )
            )
            for score in scores
        ),
        "memoryPrecisionAtK": (
            round(statistics.fmean(numbers(retrieval_scores, "precision_at_k")), 6)
            if numbers(retrieval_scores, "precision_at_k")
            else None
        ),
        "memoryRecallAtK": (
            round(statistics.fmean(numbers(retrieval_scores, "recall_at_k")), 6)
            if numbers(retrieval_scores, "recall_at_k")
            else None
        ),
        "memoryMRR": (
            round(statistics.fmean(numbers(retrieval_scores, "mrr")), 6)
            if numbers(retrieval_scores, "mrr")
            else None
        ),
        "vectorHits": sum(int(value) for value in numbers(retrieval_scores, "vector_hits")),
        "graphHits": sum(int(value) for value in numbers(retrieval_scores, "graph_hits")),
        "emptyRetrievalRate": _rate(
            bool(_score_value(score, "retrieval_empty", "retrievalEmpty", default=False))
            for score in retrieval_scores
        ),
        "directQuestionRulePassRate": _rate(direct),
        "repetitionRate": _rate(
            bool(_score_value(score, "repetition_detected", "repetitionDetected", default=False))
            for score in scores
        ),
        "p50LatencyMs": _percentile(latencies, 0.5),
        "p95LatencyMs": _percentile(latencies, 0.95),
        "tokenUsage": {
            "promptTokens": prompt_tokens,
            "completionTokens": completion_tokens,
            "totalTokens": total_tokens,
        },
        "retryCount": sum(int(value) for value in numbers(scores, "retries")),
        "estimatedCostCny": round(sum(numbers(scores, "estimated_cost_cny")), 6),
        "hardFailures": hard,
        "hardFailureRate": round(hard / len(scores), 6) if scores else None,
        "failureCounts": dict(sorted(failures.items())),
    }


def _camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def _judge_protocol_rubric(protocol: object) -> dict[str, Any]:
    """Load the Judge rubric lazily so report Rule metrics stay independent."""

    from .judge import protocol_rubric_v2

    rubric = protocol_rubric_v2(protocol)
    return {
        "protocol": str(rubric["protocol"]),
        "applicable_dimensions": tuple(
            str(value) for value in rubric["applicable_dimensions"]
        ),
        "not_applicable_dimensions": tuple(
            str(value) for value in rubric["not_applicable_dimensions"]
        ),
        "focus": tuple(str(value) for value in rubric["focus"]),
        "structured_protocol": bool(rubric["structured_protocol"]),
    }


def _judge_dimension_metrics(
    scores: Iterable[Mapping[str, Any]],
    dimensions: Iterable[str],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    score_list = list(scores)
    for name in dimensions:
        values = [
            float(raw)
            for score in score_list
            if isinstance(
                (raw := score.get(name, score.get(_camel(name)))),
                (int, float),
            )
            and not isinstance(raw, bool)
        ]
        rounded = [max(1, min(5, int(round(value)))) for value in values]
        result[_camel(name)] = {
            "mean": round(statistics.fmean(values), 6) if values else None,
            "median": round(statistics.median(values), 6) if values else None,
            "distribution": {
                str(bucket): rounded.count(bucket) for bucket in range(1, 6)
            },
        }
    return result


def _judge_metrics(cases: list[dict[str, Any]], *, enabled: bool) -> dict[str, Any]:
    scores: list[Mapping[str, Any]] = []
    protocol_scores: dict[str, list[Mapping[str, Any]]] = {}
    protocols: set[str] = set()
    pairs: list[tuple[str, Mapping[str, Any], Mapping[str, Any]]] = []
    for case in cases:
        rubric = _judge_protocol_rubric(case.get("protocol"))
        protocol = rubric["protocol"]
        protocols.add(protocol)
        runs = case.get("runs", [])
        if not isinstance(runs, list):
            continue
        for run in runs:
            if not isinstance(run, Mapping):
                continue
            values = run.get("judgeScores", [])
            if not isinstance(values, list):
                continue
            run_scores = [value for value in values if isinstance(value, Mapping)]
            scores.extend(run_scores)
            protocol_scores.setdefault(protocol, []).extend(run_scores)
            if len(run_scores) >= 2:
                pairs.append((protocol, run_scores[0], run_scores[1]))
    dimensions = (
        "persona_consistency",
        "context_faithfulness",
        "response_relevance",
        "naturalness",
        "goal_progress",
        "player_agency",
    )
    applicable_by_protocol = {
        protocol: set(_judge_protocol_rubric(protocol)["applicable_dimensions"])
        for protocol in protocols | set(protocol_scores)
    }
    applicable_scores: dict[str, list[Mapping[str, Any]]] = {
        name: [
            score
            for protocol, protocol_items in protocol_scores.items()
            if name in applicable_by_protocol.get(protocol, set())
            for score in protocol_items
        ]
        for name in dimensions
    }
    dimension_metrics = {
        _camel(name): _judge_dimension_metrics(applicable_scores[name], (name,))[
            _camel(name)
        ]
        for name in dimensions
    }
    disagreements = sum(
        1
        for case in cases
        if "judge_disagreement" in case.get("reviewReasons", [])
    )
    absolute_differences: list[float] = []
    dimension_consistency: list[bool] = []
    bool_consistency: list[bool] = []
    issue_consistency: list[bool] = []
    for protocol, first, second in pairs:
        applicable_dimensions = applicable_by_protocol.get(protocol, set())
        for name in dimensions:
            if name not in applicable_dimensions:
                continue
            left = first.get(name, first.get(_camel(name)))
            right = second.get(name, second.get(_camel(name)))
            if (
                isinstance(left, (int, float))
                and not isinstance(left, bool)
                and isinstance(right, (int, float))
                and not isinstance(right, bool)
            ):
                difference = abs(float(left) - float(right))
                absolute_differences.append(difference)
                dimension_consistency.append(difference <= 1)
        for name in (
            "contradiction_detected",
            "unsupported_claim_detected",
            "direct_question_answered",
        ):
            bool_consistency.append(
                bool(first.get(name, first.get(_camel(name), False)))
                == bool(second.get(name, second.get(_camel(name), False)))
            )
        first_issues = first.get("major_issues", first.get("majorIssues", []))
        second_issues = second.get("major_issues", second.get("majorIssues", []))
        issue_consistency.append(set(first_issues or []) == set(second_issues or []))

    metrics: list[Mapping[str, Any]] = []
    for score in scores:
        value = score.get("judgeMetrics")
        if isinstance(value, Mapping):
            metrics.append(value)

    def metric_numbers(name: str) -> list[float]:
        return [
            float(value)
            for item in metrics
            if isinstance((value := item.get(name, item.get(_camel(name)))), (int, float))
            and not isinstance(value, bool)
        ]

    confidence = Counter(str(score.get("confidence", "unknown")) for score in scores)
    schema_failures = sum(bool(score.get("judgeErrorCode")) for score in scores)
    call_values = metric_numbers("calls")
    judge_calls = sum(int(value) for value in call_values) if call_values else len(scores)
    protocol_rubrics: dict[str, dict[str, Any]] = {}
    for protocol in sorted(protocols | set(protocol_scores)):
        rubric = _judge_protocol_rubric(protocol)
        applicable = tuple(rubric["applicable_dimensions"])
        protocol_rubrics[protocol] = {
            "applicableDimensions": [_camel(name) for name in applicable],
            "notApplicableDimensions": [
                _camel(name) for name in rubric["not_applicable_dimensions"]
            ],
            "focus": list(rubric["focus"]),
            "structuredProtocol": rubric["structured_protocol"],
            "scores": len(protocol_scores.get(protocol, [])),
            "dimensions": _judge_dimension_metrics(
                protocol_scores.get(protocol, []), applicable
            ),
        }
    return {
        "judgeModel": "doubao-seed-2.1-turbo" if enabled else None,
        "rubricVersion": "agent-semantic-rubric-v2",
        "scores": len(scores),
        "casesWithScores": sum(1 for case in cases if case.get("judgeScores")),
        "dimensions": dimension_metrics,
        "protocolRubrics": protocol_rubrics,
        "applicableDimensionsByProtocol": {
            protocol: value["applicableDimensions"]
            for protocol, value in protocol_rubrics.items()
        },
        "contradictionRate": _rate(
            bool(score.get("contradiction_detected", score.get("contradictionDetected", False)))
            for score in scores
        ),
        "unsupportedClaimRate": _rate(
            bool(score.get("unsupported_claim_detected", score.get("unsupportedClaimDetected", False)))
            for score in scores
        ),
        "directAnswerRate": _rate(
            bool(score.get("direct_question_answered", score.get("directQuestionAnswered", False)))
            for score in scores
        ),
        "confidenceDistribution": dict(sorted(confidence.items())),
        "repeatPairs": len(pairs),
        "dimensionConsistencyRate": _rate(dimension_consistency),
        "meanAbsoluteScoreDifference": (
            round(statistics.fmean(absolute_differences), 6)
            if absolute_differences
            else None
        ),
        "boolConsistencyRate": _rate(bool_consistency),
        "majorIssuesConsistencyRate": _rate(issue_consistency),
        "disagreements": disagreements,
        "judgeCalls": judge_calls,
        "judgeTokenUsage": {
            "promptTokens": sum(int(value) for value in metric_numbers("prompt_tokens")),
            "completionTokens": sum(int(value) for value in metric_numbers("completion_tokens")),
            "totalTokens": sum(int(value) for value in metric_numbers("total_tokens")),
        },
        "judgeFormatRetries": sum(
            int(value) for value in metric_numbers("format_retries")
        ),
        "judgeProviderRetries": sum(
            int(value) for value in metric_numbers("provider_retries")
        ),
        "judgeRetryCount": sum(
            int(value) for value in metric_numbers("format_retries")
        )
        + sum(int(value) for value in metric_numbers("provider_retries")),
        "judgeP95LatencyMs": _percentile(metric_numbers("latency_ms"), 0.95),
        "judgeEstimatedCostCny": round(sum(metric_numbers("estimated_cost_cny")), 6),
        "judgeSchemaFailures": schema_failures,
        "judgeInjectionPassRate": None,
        "judgeAdvisory": True,
        "judgeAdvisoryReasons": ["calibration_not_available"],
        "judgeCalibrationPassRate": None,
        "judgeAdvisoryThresholds": {
            "calibrationPassRate": 0.8,
            "injectionPassRate": 1.0,
            "minimumInjectionCases": 3,
        },
    }


def _judge_calibration_advisory(calibration: object) -> dict[str, Any]:
    thresholds = {
        "calibrationPassRate": 0.8,
        "injectionPassRate": 1.0,
        "minimumInjectionCases": 3,
    }
    if not isinstance(calibration, Mapping):
        return {
            "advisory": True,
            "reasons": ["calibration_not_available"],
            "calibrationPassRate": None,
            "injectionPassRate": None,
            "injectionCases": None,
            "thresholds": thresholds,
        }

    def number(*names: str) -> float | None:
        value: object = None
        for name in names:
            if name in calibration:
                value = calibration[name]
                break
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        return None

    calibration_rate = number("calibrationPassRate", "calibration_pass_rate")
    injection_rate = number("injectionPassRate", "injection_pass_rate")
    injection_cases = number("injectionCases", "injection_cases")
    reasons: list[str] = []
    if calibration_rate is None:
        reasons.append("calibration_pass_rate_missing")
    elif calibration_rate < thresholds["calibrationPassRate"]:
        reasons.append("calibration_below_80_percent")
    if calibration.get("complete") is False:
        reasons.append("calibration_incomplete")
    if (
        injection_rate is None
        or injection_rate < thresholds["injectionPassRate"]
        or (injection_cases is not None and injection_cases < thresholds["minimumInjectionCases"])
        or injection_cases is None
    ):
        reasons.append("injection_not_3_of_3")
    return {
        "advisory": bool(reasons),
        "reasons": reasons,
        "calibrationPassRate": calibration_rate,
        "injectionPassRate": injection_rate,
        "injectionCases": int(injection_cases) if injection_cases is not None else None,
        "thresholds": thresholds,
    }


def _apply_judge_advisory(report: Mapping[str, Any]) -> None:
    metrics = report.get("llmJudgeMetrics")
    if not isinstance(metrics, dict):
        return
    advisory = _judge_calibration_advisory(report.get("judgeCalibration"))
    metrics.update(
        {
            "judgeAdvisory": advisory["advisory"],
            "judgeAdvisoryReasons": list(advisory["reasons"]),
            "judgeCalibrationPassRate": advisory["calibrationPassRate"],
            "judgeInjectionPassRate": advisory["injectionPassRate"],
            "judgeInjectionCases": advisory["injectionCases"],
            "judgeAdvisoryThresholds": advisory["thresholds"],
        }
    )


def _review_reasons(case: Mapping[str, Any]) -> list[str]:
    reasons = case.get("reviewReasons", [])
    if not isinstance(reasons, list):
        return []
    return sorted({str(item) for item in reasons if str(item)})


def _bad_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for case in cases:
        reasons = _review_reasons(case)
        score = case.get("ruleScore")
        hard = bool(_score_value(score, "hard_failure", "hardFailure", default=False))
        if not reasons and not hard:
            continue
        rule_failures = _score_value(score, "failures", default=[])
        if not isinstance(rule_failures, list):
            rule_failures = []
        combined_reasons = sorted({*reasons, *(str(item) for item in rule_failures)})
        result.append(
            {
                "caseId": str(case.get("caseId", "")),
                "category": case.get("category"),
                "protocol": case.get("protocol"),
                "hardFailure": hard,
                "reasons": combined_reasons or ["rule_hard_failure"],
            }
        )
    return sorted(result, key=lambda item: (str(item.get("caseId", "")), str(item.get("protocol", ""))))


def _review_queue(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        reasons = _review_reasons(case)
        if not reasons:
            continue
        runs = case.get("runs", [])
        first_run = runs[0] if isinstance(runs, list) and runs and isinstance(runs[0], Mapping) else {}
        judge_scores = first_run.get("judgeScores", []) if isinstance(first_run, Mapping) else []
        rows.append(
            {
                "caseId": str(case.get("caseId", "")),
                "category": case.get("category"),
                "protocol": case.get("protocol"),
                "reasons": reasons,
                "ruleFailures": list(
                    _score_value(case.get("ruleScore", {}), "failures", default=[])
                    or []
                ),
                "ruleResult": case.get("ruleScore", {}),
                "judgeResult": judge_scores[0]
                if isinstance(judge_scores, list) and judge_scores
                else None,
                "judgeDisagreement": "judge_disagreement" in reasons,
                "judgeEffectiveConfidence": case.get(
                    "judgeEffectiveConfidence",
                    "unknown",
                ),
                "candidateOutput": first_run.get("candidateSummary", "")
                if isinstance(first_run, Mapping)
                else "",
                "confirmationQuestion": "该输出是否违反角色信息边界、上下文事实或玩家自主性？",
                "humanDecision": None,
                "humanNotes": None,
            }
        )
    return sorted(rows, key=lambda item: (str(item.get("caseId", "")), str(item.get("protocol", ""))))


def build_report(
    *,
    cases: Iterable[Any],
    execution: Any,
    budget: Any,
    selected_cases: Iterable[Any] = (),
    enable_judge: bool = False,
    planned_calls: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical JSON-compatible report projection."""

    selected = list(selected_cases)
    case_by_id = {str(_case_value(case, "case_id", "caseId", "id", default="")): case for case in selected}
    safe_cases = [_safe_case_result(case, case_by_id) for case in cases]
    safe_cases.sort(key=lambda item: (str(item.get("caseId", "")), str(item.get("protocol", ""))))
    execution_dict = execution.to_dict(budget) if hasattr(execution, "to_dict") else _plain(execution)
    execution_dict = redact(execution_dict, max_text=240)
    mode = execution_dict.get("mode")
    metadata = {
        "schemaVersion": 1,
        "reportType": "agent_semantic_evaluation",
        "candidateModel": "offline-fake-candidate"
        if mode == "offline"
        else "doubao-seed-2.0-lite",
        "judgeModel": "doubao-seed-2.1-turbo" if enable_judge else None,
        "judgeEnabled": bool(enable_judge),
        "selectedCaseIds": sorted(str(_case_value(case, "case_id", "caseId", "id", default="")) for case in selected),
        "selectedCategories": sorted({str(_case_value(case, "category", default="")) for case in selected}),
    }
    if planned_calls is not None:
        execution_dict["plannedCalls"] = redact(dict(planned_calls), max_text=240)
    rule_metrics = _rule_metrics(safe_cases)
    judge_metrics = _judge_metrics(safe_cases, enabled=enable_judge)
    hard_failures = int(rule_metrics["hardFailures"])
    reviewed = sum(1 for case in safe_cases if _review_reasons(case))
    combined = {
        "cases": len(safe_cases),
        "passed": sum(
            1
            for case in safe_cases
            if not _review_reasons(case)
            and not bool(_score_value(case.get("ruleScore", {}), "hard_failure", "hardFailure", default=False))
        ),
        "hardFailures": hard_failures,
        "reviewRequired": reviewed,
        "complete": bool(execution_dict.get("complete", False)),
        "ruleHardGatePrecedesJudge": True,
    }
    report = {
        "metadata": metadata,
        "execution": execution_dict,
        "ruleBasedMetrics": rule_metrics,
        "llmJudgeMetrics": judge_metrics,
        "combinedResult": combined,
        "cases": safe_cases,
        "badCases": _bad_cases(safe_cases),
        "reviewQueue": _review_queue(safe_cases),
    }
    safe_report = redact(report, max_text=240)
    # Keep the artifact contract executable: if a new output key is added to
    # this builder without a corresponding model field, fail at construction
    # time instead of emitting an artifact that consumers cannot validate.
    EvaluationReport.model_validate(safe_report)
    return safe_report


def report_to_json(report: Mapping[str, Any], *, indent: int | None = 2) -> str:
    """Serialize a report with deterministic key and list ordering."""

    _apply_judge_advisory(report)
    return json.dumps(report, ensure_ascii=False, sort_keys=True, indent=indent) + "\n"


def render_json(report: Mapping[str, Any], *, indent: int | None = 2) -> str:
    return report_to_json(report, indent=indent)


def _metric(report: Mapping[str, Any], section: str, key: str, default: Any = None) -> Any:
    value = report.get(section, {})
    return value.get(key, default) if isinstance(value, Mapping) else default


def report_to_markdown(report: Mapping[str, Any]) -> str:
    """Render a concise stable report without dialogue or private context."""

    _apply_judge_advisory(report)
    execution = report.get("execution", {})
    combined = report.get("combinedResult", {})
    rule = report.get("ruleBasedMetrics", {})
    judge = report.get("llmJudgeMetrics", {})
    lines = [
        "# Agent semantic evaluation",
        "",
        f"- Mode: `{execution.get('mode', 'unknown')}`",
        f"- Complete: `{execution.get('complete', False)}`",
        f"- Cases: `{execution.get('completedCases', 0)}/{execution.get('selectedCases', 0)}`",
        f"- Candidate calls: `{execution.get('candidateCalls', 0)}`",
        f"- Judge calls: `{execution.get('judgeCalls', 0)}`",
        f"- Embedding calls: `{execution.get('embeddingCalls', 0)}`",
        f"- Estimated cost CNY: `{execution.get('estimatedCostCny', 0.0)}`",
        f"- Budget exhausted: `{execution.get('budgetExhausted', False)}`",
        f"- Timed out: `{execution.get('timedOut', False)}`",
        "",
        "| Cases | Passed | Hard failures | Review required |",
        "|---:|---:|---:|---:|",
        f"| {combined.get('cases', 0)} | {combined.get('passed', 0)} | {combined.get('hardFailures', 0)} | {combined.get('reviewRequired', 0)} |",
        "",
        "## Deterministic rules",
        "",
        f"- Schema success: `{rule.get('schemaSuccessRate')}`",
        f"- Owner/canary/internal leaks: `{rule.get('ownerLeakCount')}/{rule.get('canaryLeakCount')}/{rule.get('internalFieldLeakCount')}`",
        f"- Memory Precision@K / Recall@K / MRR: `{rule.get('memoryPrecisionAtK')} / {rule.get('memoryRecallAtK')} / {rule.get('memoryMRR')}`",
        f"- Direct-question pass / repetition: `{rule.get('directQuestionRulePassRate')} / {rule.get('repetitionRate')}`",
        f"- Candidate P50 / P95 ms: `{rule.get('p50LatencyMs')} / {rule.get('p95LatencyMs')}`",
        "",
        "## LLM Judge",
        "",
        f"- Model / rubric: `{judge.get('judgeModel')} / {judge.get('rubricVersion')}`",
        f"- Scores / repeat pairs: `{judge.get('scores')} / {judge.get('repeatPairs')}`",
        f"- Dimension consistency / mean |Δ|: `{judge.get('dimensionConsistencyRate')} / {judge.get('meanAbsoluteScoreDifference')}`",
        f"- Contradiction / unsupported claim / direct answer: `{judge.get('contradictionRate')} / {judge.get('unsupportedClaimRate')} / {judge.get('directAnswerRate')}`",
        f"- Schema failures / injection pass: `{judge.get('judgeSchemaFailures')} / {judge.get('judgeInjectionPassRate')}`",
        f"- Judge advisory: `{judge.get('judgeAdvisory')}` ({', '.join(str(value) for value in (judge.get('judgeAdvisoryReasons') or []))})",
        f"- Format / provider / total retries: `{judge.get('judgeFormatRetries')} / {judge.get('judgeProviderRetries')} / {judge.get('judgeRetryCount')}`",
        "",
        "## Bad cases",
        "",
    ]
    bad_cases = report.get("badCases", [])
    if not isinstance(bad_cases, list) or not bad_cases:
        lines.append("None.")
    else:
        lines.extend(
            [
                "| Case | Category | Protocol | Hard failure | Reasons |",
                "|---|---|---|---|---|",
            ]
        )
        for item in bad_cases:
            if not isinstance(item, Mapping):
                continue
            reasons = ", ".join(str(value) for value in item.get("reasons", []))
            lines.append(
                f"| `{item.get('caseId', '')}` | `{item.get('category', '')}` | `{item.get('protocol', '')}` | `{item.get('hardFailure', False)}` | `{reasons}` |"
            )
    return "\n".join(lines) + "\n"


def render_markdown(report: Mapping[str, Any]) -> str:
    return report_to_markdown(report)


def bad_cases(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = report.get("badCases", [])
    return [dict(item) for item in value if isinstance(item, Mapping)]


def render_bad_cases(report: Mapping[str, Any]) -> str:
    """Render only bad cases, suitable for a small CI artifact."""

    items = bad_cases(report)
    lines = ["# Bad cases", ""]
    if not items:
        lines.append("None.")
    else:
        for item in items:
            reasons = ", ".join(str(value) for value in item.get("reasons", []))
            lines.append(f"- `{item.get('caseId', '')}`: {reasons}")
    return "\n".join(lines) + "\n"


def review_queue(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = report.get("reviewQueue", [])
    return [dict(item) for item in value if isinstance(item, Mapping)]


def human_arbitration_table(report: Mapping[str, Any]) -> str:
    """Render a stable manual arbitration queue with blank decision columns."""

    lines = [
        "# Human arbitration queue",
        "",
        "| Case | Category | Protocol | Reasons | Rule failures | Judge | Candidate | Confirmation | Decision | Notes |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for item in review_queue(report):
        reasons = ", ".join(str(value) for value in item.get("reasons", []))
        failures = ", ".join(str(value) for value in item.get("ruleFailures", []))
        judge = json.dumps(item.get("judgeResult"), ensure_ascii=False, sort_keys=True)
        candidate = str(item.get("candidateOutput", "")).replace("|", "\\|")
        question = str(item.get("confirmationQuestion", "")).replace("|", "\\|")
        lines.append(
            f"| `{item.get('caseId', '')}` | `{item.get('category', '')}` | `{item.get('protocol', '')}` | `{reasons}` | `{failures}` | `{judge}` | {candidate} | {question} |  |  |"
        )
    if len(lines) == 4:
        lines.append("| _none_ |  |  |  |  |  |  |  |  |  |")
    return "\n".join(lines) + "\n"


def render_review_queue(report: Mapping[str, Any]) -> str:
    return human_arbitration_table(report)


def render_judge_stability(report: Mapping[str, Any]) -> str:
    metrics = report.get("llmJudgeMetrics", {})
    return "\n".join(
        [
            "# Judge stability report",
            "",
            f"- Model: `{metrics.get('judgeModel')}`",
            f"- Repeat pairs: `{metrics.get('repeatPairs')}`",
            f"- Dimension consistency rate (|Δ| ≤ 1): `{metrics.get('dimensionConsistencyRate')}`",
            f"- Mean absolute score difference: `{metrics.get('meanAbsoluteScoreDifference')}`",
            f"- Boolean consistency rate: `{metrics.get('boolConsistencyRate')}`",
            f"- Major-issue consistency rate: `{metrics.get('majorIssuesConsistencyRate')}`",
            f"- Disagreements requiring review: `{metrics.get('disagreements')}`",
            f"- Confidence distribution: `{metrics.get('confidenceDistribution')}`",
            "",
        ]
    )


def write_report(
    report: Mapping[str, Any],
    output: str | Path,
    *,
    write_markdown: bool = True,
    write_bad_cases: bool = True,
    write_review_queue: bool = True,
    write_judge_stability: bool = True,
) -> list[Path]:
    """Write JSON plus optional companion artifacts and return their paths."""

    # CLI adds the optional ``judgeCalibration`` section after ``build_report``
    # returns.  Validate here as the final artifact boundary as well, so a
    # newly added CLI field cannot silently produce an incompatible JSON file.
    _apply_judge_advisory(report)
    EvaluationReport.model_validate(report)
    path = Path(output)
    if path.suffix.lower() == ".json":
        json_path = path
        stem = path.with_suffix("")
    else:
        path.mkdir(parents=True, exist_ok=True)
        json_path = path / "agent_semantic_evaluation.json"
        stem = path / "agent_semantic_evaluation"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(report_to_json(report), encoding="utf-8")
    paths = [json_path]
    if write_markdown:
        markdown_path = stem.with_suffix(".md")
        markdown_path.write_text(report_to_markdown(report), encoding="utf-8")
        paths.append(markdown_path)
    if write_bad_cases:
        bad_path = stem.with_name(stem.name + "_bad_cases").with_suffix(".md")
        bad_path.write_text(render_bad_cases(report), encoding="utf-8")
        paths.append(bad_path)
    if write_review_queue:
        review_path = stem.with_name(stem.name + "_human_arbitration").with_suffix(".md")
        review_path.write_text(human_arbitration_table(report), encoding="utf-8")
        paths.append(review_path)
    if write_judge_stability:
        stability_path = stem.with_name(stem.name + "_judge_stability").with_suffix(".md")
        stability_path.write_text(render_judge_stability(report), encoding="utf-8")
        paths.append(stability_path)
    return paths


__all__ = [
    "bad_cases",
    "build_report",
    "human_arbitration_table",
    "redact",
    "render_bad_cases",
    "render_json",
    "render_judge_stability",
    "render_markdown",
    "render_review_queue",
    "report_to_json",
    "report_to_markdown",
    "review_queue",
    "write_report",
]
