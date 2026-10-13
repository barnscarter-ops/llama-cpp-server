"""Small statistics: medians, noise floor, Wilson interval, paired bootstrap."""

from __future__ import annotations

import math
import random
import statistics
from typing import Sequence

MIN_NOISE_FLOOR = 0.03


def median(xs: Sequence[float]) -> float:
    return statistics.median(xs)


def mad(xs: Sequence[float]) -> float:
    m = median(xs)
    return median([abs(x - m) for x in xs])


def weighted_geomean(values: Sequence[float], weights: Sequence[float]) -> float:
    if len(values) != len(weights) or not values:
        raise ValueError("values and weights must match and be non-empty")
    total = sum(weights)
    return math.exp(sum(w * math.log(v) for v, w in zip(values, weights)) / total)


def noise_floor(baseline_scores: Sequence[float]) -> float:
    """max(3%, 2x the relative spread of the baseline medians)."""
    if len(baseline_scores) < 2:
        return MIN_NOISE_FLOOR
    m = median(baseline_scores)
    spread = (max(baseline_scores) - min(baseline_scores)) / m if m else 0.0
    return max(MIN_NOISE_FLOOR, 2.0 * spread)


def wilson(passes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = passes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def paired_bootstrap(a: Sequence[int], b: Sequence[int], iters: int = 2000, seed: int = 7) -> tuple[float, float, float]:
    """Mean(a-b) over the same items with a 95% bootstrap interval."""
    if len(a) != len(b) or not a:
        raise ValueError("paired samples must be the same non-zero length")
    diffs = [x - y for x, y in zip(a, b)]
    rng = random.Random(seed)
    n = len(diffs)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(iters))
    return (sum(diffs) / n, means[int(0.025 * iters)], means[int(0.975 * iters) - 1])


def percentile(xs: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile, q in [0,1]."""
    s = sorted(xs)
    if not s:
        raise ValueError("empty")
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)
