# arena-kit

Shared plumbing for [arenas](../README.md). An arena supplies three things:

- **items:** a list of dicts, each with an `"id"` (and optional `"tags"`);
- **contenders:** async functions `item -> Call(output, latency_ms, cost_usd, cached)`;
- **score:** `score(item, output) -> bool`.

Everything else is here:

| Module | What it does |
|---|---|
| `client` | OpenRouter `Client` with a disk cache keyed by the exact request (reruns only pay for what's new), rate limits, and two retry policies: `transient` (retry 429/5xx/timeouts) and `unbilled` (retry only failures the provider can't have billed). |
| `mock` | `MockClient` with the same interface, plus `noisy_answer` for deterministic fake accuracy, so every pipeline runs offline. |
| `runner` | Runs every contender over every item with per-contender concurrency; failed calls are recorded as wrong answers, not dropped. `metrics` gives quality, p50/p95 (live calls only), cost per 1k (unknown stays unknown). |
| `stats` | Exact McNemar, sign test, seeded paired bootstrap, percentiles. |
| `recommend` | The fixed rule: tied = within 2 points of the best and not significantly worse; pick = cheapest + fastest tied contender. |
| `snapshot` | `snapshots/<date>/{README.md,report.json,summary.json}`, the snapshot index, and the README pointer block. |
| `contract` | `arena-summary/1`: build, validate, write `latest.json`, refresh from `arena.yaml`. |
| `cli` | `arena-kit new/validate/refresh`, and `arena_main(ArenaSpec)`, which gives an arena `list`, `run [--mock]`, `show` and `snapshot`. |

## Minimal arena

```python
from arena_kit import ArenaSpec, Call, Contender, arena_main

async def baseline(item):
    return Call(output="yes", latency_ms=0.1, cost_usd=0.0)

SPEC = ArenaSpec(
    root=ROOT,
    load_items=lambda: [{"id": "1", "expected": "yes"}],
    load_contenders=lambda mock: [Contender("always-yes", baseline, kind="rules")],
    score=lambda item, output: output == item["expected"],
)

def main():
    arena_main(SPEC)
```

`arena-kit new <name> --question "..."` writes a complete version of this (YAML contenders, an
LLM contender with mock mode, a starter dataset, `arena.yaml`, tests).

## Using only the contract

Arenas with their own runner (most of the original ones) call `contract.build(...)` and
`contract.write(...)` from their snapshot step. Arenas in other languages write JSON that
validates against [`summary.schema.json`](src/arena_kit/summary.schema.json), and can check it
with `arena-kit validate snapshots/latest.json`.

## Install

```toml
dependencies = ["arena-kit @ git+https://github.com/SeijiMatsumoto/arenas.git#subdirectory=kit"]

[tool.hatch.metadata]
allow-direct-references = true
```
