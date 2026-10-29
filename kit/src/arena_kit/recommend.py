"""Deterministic picks from a report's numbers. No LLM: the same report always gives the same answer.

The rules, in order:
1. Contenders with failed calls are excluded: a pipeline that errors isn't a candidate.
2. The top contender has the best quality (ties: faster).
3. A contender is "tied" when it is within TOLERANCE of the top quality AND an exact McNemar test
   on the paired items does not show it is worse (p < ALPHA). The test guards against trusting
   noise on big datasets; the tolerance guards against small ones, where even a 10-point gap
   can fail to reach significance.
4. Among tied contenders, the pick has the best combined rank on cost and latency (ties go to
   quality). Unknown cost or latency ranks last, never as free.
5. Contenders significantly worse than the top are marked "worse".
"""

from __future__ import annotations

import math
from typing import Any

from .stats import paired_p

ALPHA = 0.05
TOLERANCE = 0.02

RULES = (
    f"Tied = within {TOLERANCE:.0%} of the best quality and not significantly worse "
    f"(exact McNemar, p < {ALPHA}); pick = cheapest + fastest tied contender."
)


def _rank(values: dict[str, float | None]) -> dict[str, int]:
    """Dense rank, ascending: equal values share a rank, unknown values rank last."""
    distinct = sorted({v for v in values.values() if v is not None})
    return {m: distinct.index(v) if v is not None else len(distinct) for m, v in values.items()}


def recommend(stats: dict[str, dict[str, Any]], *, tolerance: float = TOLERANCE,
              alpha: float = ALPHA) -> dict[str, Any]:
    """`stats` is `runner.metrics(report)`. Returns pick, verdict per contender and p-values."""
    verdicts = {name: "excluded" for name, s in stats.items() if s["errors"]}
    live = {n: s for n, s in stats.items() if n not in verdicts and s["quality"] is not None}
    if not live:
        return {"pick": None, "top": None, "reason": "No contender finished without errors.",
                "verdicts": verdicts, "p_vs_top": {}, "rules": RULES}

    top = max(live, key=lambda n: (live[n]["quality"], -(live[n]["p50_ms"] or math.inf)))
    p_vs_top = {n: paired_p(live[top]["correct"], s["correct"]) for n, s in live.items()}
    tied = [n for n, s in live.items()
            if s["quality"] >= live[top]["quality"] - tolerance - 1e-9
            and not (n != top and p_vs_top[n] < alpha)]
    cost = _rank({n: live[n]["cost_per_1k_usd"] for n in tied})
    speed = _rank({n: live[n]["p50_ms"] for n in tied})
    pick = min(tied, key=lambda n: (cost[n] + speed[n], -live[n]["quality"], n))

    for n in live:
        if n == pick:
            verdicts[n] = "pick"
        elif n in tied:
            verdicts[n] = "tied"
        elif p_vs_top[n] < alpha:
            verdicts[n] = "worse"
        else:
            verdicts[n] = "other"
    reason = ("Best quality, and nothing ties it." if pick == top and len(tied) == 1 else
              f"Cheapest and fastest of {len(tied)} contenders tied with the best quality.")
    return {"pick": pick, "top": top, "reason": reason, "verdicts": verdicts,
            "p_vs_top": p_vs_top, "rules": RULES}
