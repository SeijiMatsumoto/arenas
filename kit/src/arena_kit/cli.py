"""Two CLIs.

`arena-kit` (this package's own): `new` scaffolds an arena, `validate` checks a summary.json,
`refresh` re-applies arena.yaml to the latest summary.

`arena` (each arena's): build an `ArenaSpec` and call `arena_main(spec)` to get
`list`, `run`, `show` and `snapshot` with no further code.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress
from rich.table import Table

from . import contract, scaffold, snapshot
from .recommend import recommend
from .runner import Contender, Item, Score, latest_report, run, save_report
from .runner import metrics as compute_metrics

console = Console()


@dataclass
class ArenaSpec:
    root: Path
    load_items: Callable[[], list[Item]]
    load_contenders: Callable[[bool], list[Contender]]  # argument: mock
    score: Score
    metric: dict[str, Any] = field(default_factory=lambda: dict(snapshot.DEFAULT_METRIC))

    @property
    def reports(self) -> Path:
        return self.root / "reports"


def _select(contenders: list[Contender], only: list[str] | None) -> list[Contender]:
    if not only:
        return contenders
    unknown = set(only) - {c.name for c in contenders}
    if unknown:
        raise SystemExit(f"unknown contender(s): {', '.join(sorted(unknown))}")
    return [c for c in contenders if c.name in only]


def show_table(report: dict[str, Any], metric: dict[str, Any]) -> None:
    stats = compute_metrics(report)
    rec = recommend(stats)
    table = Table(title=f"{report['created_at']}{'  (mock)' if report['mock'] else ''}")
    for col in ("Contender", metric["label"], "p50", "p95", "Cost / 1k", "Errors", "Verdict"):
        table.add_column(col, justify="left" if col in ("Contender", "Verdict") else "right")
    for row in snapshot.contender_rows(stats, rec):
        s = stats[row["name"]]
        table.add_row(row["name"], snapshot.fmt_quality(s["quality"], metric),
                      snapshot.ms(s["p50_ms"]), snapshot.ms(s["p95_ms"]),
                      snapshot.usd(s["cost_per_1k_usd"]), str(s["errors"]), row["verdict"])
    console.print(table)
    console.print(snapshot.headline(stats, rec, metric))


def arena_main(spec: ArenaSpec, argv: list[str] | None = None) -> None:
    load_dotenv(spec.root / ".env")
    parser = argparse.ArgumentParser(prog="arena")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="show contenders and dataset size")
    p_run = sub.add_parser("run", help="run contenders over the dataset")
    p_run.add_argument("--mock", action="store_true", help="offline mock providers, no cost")
    p_run.add_argument("--only", nargs="+", help="contender names to run")
    p_show = sub.add_parser("show", help="print a report's table")
    p_show.add_argument("report", nargs="?", type=Path)
    p_snap = sub.add_parser("snapshot", help="save the latest real run as a dated snapshot")
    p_snap.add_argument("report", nargs="?", type=Path)
    p_snap.add_argument("--allow-mock", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "list":
        items = spec.load_items()
        console.print(f"{len(items)} items")
        for c in spec.load_contenders(False):
            console.print(f"  {c.name}  [dim]{c.kind}[/dim]")
        return

    if args.command == "run":
        items = spec.load_items()
        contenders = _select(spec.load_contenders(args.mock), args.only)
        with Progress(console=console) as progress:
            task = progress.add_task("running", total=len(items) * len(contenders))
            report = asyncio.run(run(contenders, items, spec.score, mock=args.mock,
                                     on_done=lambda: progress.advance(task)))
        path = save_report(report, spec.reports)
        console.print(f"saved {path.relative_to(spec.root)}")
        show_table(report, spec.metric)
        return

    path = args.report or latest_report(spec.reports, allow_mock=args.command == "show" or
                                        getattr(args, "allow_mock", False))
    if path is None:
        raise SystemExit("no report found; run `arena run` first")
    report = json.loads(path.read_text())
    if args.command == "show":
        show_table(report, spec.metric)
    elif report["mock"] and not args.allow_mock:
        raise SystemExit("refusing to snapshot a mock run (pass --allow-mock to try the pipeline)")
    else:
        folder = snapshot.take(spec.root, report, metric=spec.metric)
        console.print(f"wrote {folder.relative_to(spec.root)} and snapshots/latest.json")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="arena-kit")
    sub = parser.add_subparsers(dest="command", required=True)
    p_new = sub.add_parser("new", help="scaffold a new arena that runs offline out of the box")
    p_new.add_argument("name", help="e.g. dedup-arena")
    p_new.add_argument("--question", required=True, help="the decision this arena answers")
    p_new.add_argument("--dir", type=Path, default=Path.cwd())
    p_new.add_argument("--owner", default="SeijiMatsumoto", help="GitHub owner for repo links")
    p_val = sub.add_parser("validate", help="check summary.json files against arena-summary/1")
    p_val.add_argument("paths", nargs="+", type=Path)
    p_ref = sub.add_parser("refresh", help="re-apply arena.yaml to snapshots/latest.json")
    p_ref.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)

    if args.command == "new":
        target = scaffold.create(args.name, args.question, args.dir, owner=args.owner)
        console.print(f"created {target}\n  cd {target} && uv sync && uv run arena run --mock")
    elif args.command == "validate":
        failed = False
        for path in args.paths:
            try:
                contract.validate(json.loads(path.read_text()))
                console.print(f"[green]ok[/green] {path}")
            except contract.ContractError as error:
                failed = True
                console.print(f"[red]invalid[/red] {path}\n{error}")
        sys.exit(1 if failed else 0)
    elif args.command == "refresh":
        console.print(f"updated {contract.refresh(args.root)}")
