"""Paired significance tests and summary statistics. Pure functions, no dependencies."""

from __future__ import annotations

import math
import random
import statistics


def mcnemar_p(b: int, c: int) -> float:
    """Exact two-sided McNemar p-value from the two discordant counts."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(min(b, c) + 1)) / 2**n
    return min(1.0, 2 * tail)


def paired_p(a: dict[str, bool], b: dict[str, bool]) -> float:
    """McNemar on the items both contenders answered."""
    shared = a.keys() & b.keys()
    only_a = sum(1 for k in shared if a[k] and not b[k])
    only_b = sum(1 for k in shared if b[k] and not a[k])
    return mcnemar_p(only_a, only_b)


def sign_test_p(a: dict[str, float], b: dict[str, float]) -> float:
    """Exact sign test on paired scores (e.g. per-source accuracy); ties dropped."""
    shared = a.keys() & b.keys()
    wins = sum(1 for k in shared if a[k] > b[k])
    losses = sum(1 for k in shared if b[k] > a[k])
    return mcnemar_p(wins, losses)


def bootstrap_diff_ci(a: dict[str, float], b: dict[str, float], *, n: int = 2000,
                      level: float = 0.95, seed: int = 0) -> tuple[float, float, float]:
    """(mean diff a-b, low, high) by paired bootstrap. Fixed seed: same data, same interval."""
    keys = sorted(a.keys() & b.keys())
    if not keys:
        return (math.nan, math.nan, math.nan)
    diffs = [a[k] - b[k] for k in keys]
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(diffs, k=len(diffs))) for _ in range(n))
    lo = means[int((1 - level) / 2 * n)]
    hi = means[min(n - 1, int((1 + level) / 2 * n))]
    return statistics.fmean(diffs), lo, hi


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[round(q * 100) - 1]
