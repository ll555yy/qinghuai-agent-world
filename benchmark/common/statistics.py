from __future__ import annotations

import math
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass


def percentile(values: Iterable[float], percent: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if not 0 <= percent <= 100:
        raise ValueError("percent must be in [0, 100]")
    position = (len(ordered) - 1) * percent / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


@dataclass(frozen=True, slots=True)
class PairedEffect:
    mean_delta: float
    confidence_interval_95: tuple[float, float]
    sample_size: int
    bootstrap_samples: int


def paired_bootstrap(
    treatment: Sequence[float],
    control: Sequence[float],
    *,
    samples: int = 10_000,
    seed: int = 20260901,
) -> PairedEffect:
    if len(treatment) != len(control) or not treatment:
        raise ValueError("paired samples must be non-empty and equally sized")
    if samples < 100:
        raise ValueError("at least 100 bootstrap samples are required")
    deltas = [float(left) - float(right) for left, right in zip(treatment, control, strict=True)]
    rng = random.Random(seed)
    boot = [
        sum(deltas[rng.randrange(len(deltas))] for _ in deltas) / len(deltas)
        for _ in range(samples)
    ]
    return PairedEffect(
        mean_delta=sum(deltas) / len(deltas),
        confidence_interval_95=(percentile(boot, 2.5) or 0.0, percentile(boot, 97.5) or 0.0),
        sample_size=len(deltas),
        bootstrap_samples=samples,
    )


def cohen_kappa(left: Sequence[str], right: Sequence[str]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("annotation vectors must be non-empty and equally sized")
    labels = set(left) | set(right)
    observed = sum(a == b for a, b in zip(left, right, strict=True)) / len(left)
    expected = sum((left.count(label) / len(left)) * (right.count(label) / len(right)) for label in labels)
    return 1.0 if expected == 1.0 and observed == 1.0 else (observed - expected) / (1.0 - expected)


def recommended_paired_seeds(
    deltas: Sequence[float], *, target_half_width: float = 0.10, minimum: int = 10, maximum: int = 30
) -> int:
    if len(deltas) < 2:
        return minimum
    mean = sum(deltas) / len(deltas)
    variance = sum((value - mean) ** 2 for value in deltas) / (len(deltas) - 1)
    estimate = math.ceil((1.96 * math.sqrt(variance) / target_half_width) ** 2)
    return max(minimum, min(maximum, estimate))


__all__ = ["PairedEffect", "cohen_kappa", "paired_bootstrap", "percentile", "recommended_paired_seeds"]
