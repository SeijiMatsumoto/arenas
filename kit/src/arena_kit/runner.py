"""Run every contender over every item behind one interface, then score quality, latency, cost.

An item is a dict with at least an "id" (and optionally "tags"). A contender is an async
function item -> Call. `score(item, output)` says whether the output is correct. Everything an
arena does differently lives in those three pieces; the rest is here.
"""

from __future__ import annotations

import asyncio
import json
import statistics
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .client import Call
from .stats import percentile

Item = dict[str, Any]
Score = Callable[[Item, Any], bool]

REPORT_SCHEMA = "arena-report/1"


@dataclass
class Contender:
    name: str
    fn: Callable[[Item], Awaitable[Call]]
    kind: str = "llm"
    concurrency: int = 8


async def _run_one(contender: Contender, items: list[Item], score: Score,
                   on_done: Callable[[], None] | None) -> dict[str, Any]:
    gate = asyncio.Semaphore(contender.concurrency)

    async def one(item: Item) -> tuple[str, dict[str, Any]]:
        async with gate:
            try:
                call = await contender.fn(item)
                row = {"output": call.output, "correct": bool(score(item, call.output)),
                       "latency_ms": call.latency_ms, "cost_usd": call.cost_usd,
                       "cached": call.cached}
            except Exception as error:  # noqa: BLE001 -- a failed call is a failed answer, recorded not hidden
                row = {"error": f"{type(error).__name__}: {error}"[:300], "correct": False}
        if on_done:
            on_done()
        return str(item["id"]), row

    rows = dict(await asyncio.gather(*(one(i) for i in items)))
    return {"kind": contender.kind, "rows": rows}


async def run(contenders: list[Contender], items: list[Item], score: Score, *, mock: bool,
              on_done: Callable[[], None] | None = None, meta: dict[str, Any] | None = None
              ) -> dict[str, Any]:
    results = await asyncio.gather(*(_run_one(c, items, score, on_done) for c in contenders))
    return {
        "schema": REPORT_SCHEMA,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "mock": mock,
        "meta": meta or {},
        "items": [{"id": str(i["id"]), "tags": i.get("tags", [])} for i in items],
        "contenders": {c.name: r for c, r in zip(contenders, results)},
    }


def contender_metrics(entry: dict[str, Any]) -> dict[str, Any]:
    rows = entry["rows"]
    ok = [r for r in rows.values() if "error" not in r]
    live = [r["latency_ms"] for r in ok if not r.get("cached")]
    lat = live or [r["latency_ms"] for r in ok]
    costs = [r["cost_usd"] for r in ok if r.get("cost_usd") is not None]
    return {
        "kind": entry["kind"],
        "n": len(rows),
        "quality": sum(r["correct"] for r in rows.values()) / len(rows) if rows else None,
        "errors": len(rows) - len(ok),
        "p50_ms": statistics.median(lat) if lat else None,
        "p95_ms": percentile(lat, 0.95),
        "latency_cached_only": bool(lat) and not live,
        "cost_per_1k_usd": 1000 * statistics.fmean(costs) if costs else None,
        "correct": {k: r["correct"] for k, r in rows.items()},
    }


def metrics(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {name: contender_metrics(e) for name, e in report["contenders"].items()}


def save_report(report: dict[str, Any], reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = report["created_at"].replace(":", "").replace("-", "").split("+")[0]
    path = reports_dir / f"run-{stamp}{'-mock' if report['mock'] else ''}.json"
    path.write_text(json.dumps(report, indent=1))
    return path


def latest_report(reports_dir: Path, *, allow_mock: bool = True) -> Path | None:
    paths = sorted(reports_dir.glob("run-*.json"), reverse=True)
    if not allow_mock:
        paths = [p for p in paths if not p.stem.endswith("-mock")]
    return paths[0] if paths else None
