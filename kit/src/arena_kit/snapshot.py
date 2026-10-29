"""Dated snapshots: snapshots/<date>/{report.json,README.md,summary.json}, an index, and the
marked block at the top of the arena README that points at the newest one."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from . import contract
from .recommend import recommend
from .runner import metrics as compute_metrics

START = "<!-- snapshot:start -->"
END = "<!-- snapshot:end -->"

DEFAULT_METRIC = {"name": "accuracy", "label": "Accuracy", "higher_is_better": True,
                  "format": "percent"}


def pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.0%}"


def ms(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{v:.0f} ms" if v < 1000 else f"{v / 1000:.1f} s"


def usd(v: float | None) -> str:
    if v is None:
        return "n/a"
    return "$0" if v == 0 else f"${v:.4f}" if v < 1 else f"${v:.2f}"


def fmt_quality(v: float | None, metric: dict[str, Any]) -> str:
    if v is None:
        return "n/a"
    return pct(v) if metric["format"] == "percent" else f"{v:.3g}"


def snapshot_id(created_at: str) -> str:
    return datetime.fromisoformat(created_at).strftime("%Y-%m-%d_%H%M")


def headline(stats: dict[str, dict[str, Any]], rec: dict[str, Any], metric: dict[str, Any]) -> str:
    pick, top = rec["pick"], rec["top"]
    if pick is None:
        return "No recommendation: every contender had failed calls."
    s = stats[pick]
    parts = [f"{fmt_quality(s['quality'], metric)} {metric['label'].lower()}",
             f"{ms(s['p50_ms'])} median"]
    if s["cost_per_1k_usd"] is not None:
        parts.append(f"{usd(s['cost_per_1k_usd'])} per 1k calls")
    text = f"Use **{pick}** ({', '.join(parts)})."
    if top != pick:
        t = stats[top]
        text += f" No significant {metric['label'].lower()} gap to the top scorer, {top}"
        speedup = t["p50_ms"] / s["p50_ms"] if t["p50_ms"] and s["p50_ms"] else None
        cheaper = (t["cost_per_1k_usd"] / s["cost_per_1k_usd"]
                   if t["cost_per_1k_usd"] and s["cost_per_1k_usd"] else None)
        extras = ([f"{speedup:.1f}× faster"] if speedup and speedup > 1.05 else []) + (
            [f"{cheaper:.1f}× cheaper"] if cheaper and cheaper > 1.05 else [])
        text += f"; {pick} is {' and '.join(extras)}." if extras else "."
    return text


def contender_rows(stats: dict[str, dict[str, Any]], rec: dict[str, Any]) -> list[dict[str, Any]]:
    order = sorted(stats, key=lambda n: (-(stats[n]["quality"] or 0), n))
    return [{"name": n, "kind": stats[n]["kind"], "quality": stats[n]["quality"],
             "latency_ms": stats[n]["p50_ms"], "cost_per_1k_usd": stats[n]["cost_per_1k_usd"],
             "errors": stats[n]["errors"], "verdict": rec["verdicts"].get(n, "other")}
            for n in order]


def render(summary: dict[str, Any]) -> str:
    m = summary["metric"]
    lines = [
        (f"# {summary['arena'].get('title', summary['arena']['name'])}: "
        f"{snapshot_id(summary['snapshot']['created_at'])}"),
        "",
        f"**{summary['arena']['question']}**",
        "",
        summary["headline"],
        "",
        f"| Contender | {m['label']} | Median latency | Cost per 1k | Errors | Verdict |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for c in summary["contenders"]:
        lines.append(f"| {c['name']} | {fmt_quality(c['quality'], m)} | {ms(c['latency_ms'])} | "
                     f"{usd(c['cost_per_1k_usd'])} | {c.get('errors', 0)} | {c['verdict']} |")
    labels = summary["data"]["labels"]
    lines += ["", "## Method", "",
              f"- **Items:** {summary['data']['items']}. "
              f"{summary['data'].get('description', '')}".rstrip(),
              f"- **Labels:** {labels['source']}. {labels.get('note', '')}".rstrip(),
              f"- **Rule:** {summary['method']['rules']}",
              ("- Latency is the median of live calls only (cache hits keep their original "
              "latency and are excluded). Unknown cost is shown as n/a, never $0.")]
    if summary["mock"]:
        lines += ["", "> **Mock run.** These numbers come from mock providers and mean nothing."]
    if summary["caveats"]:
        lines += ["", "## Caveats", ""] + [f"- {c}" for c in summary["caveats"]]
    return "\n".join(lines) + "\n"


def take(root: Path, report: dict[str, Any], *, metric: dict[str, Any] | None = None,
         snapshots_dir: Path | None = None) -> Path:
    """Snapshot a kit-native report: README, report.json, summary.json, index, README pointer."""
    metric = metric or DEFAULT_METRIC
    snapshots_dir = snapshots_dir or root / "snapshots"
    stats = compute_metrics(report)
    rec = recommend(stats)
    sid = snapshot_id(report["created_at"])
    folder = snapshots_dir / sid
    folder.mkdir(parents=True, exist_ok=True)
    summary = contract.build(
        contract.load_manifest(root), snapshot_id=sid, created_at=report["created_at"],
        snapshot_path=folder.relative_to(root).as_posix(),
        headline=headline(stats, rec, metric),
        pick={"name": rec["pick"], "reason": rec["reason"]} if rec["pick"] else None,
        metric=metric, contenders=contender_rows(stats, rec), items=len(report["items"]),
        rules=rec["rules"], mock=report["mock"],
    )
    (folder / "report.json").write_text(json.dumps(report, separators=(",", ":")))
    (folder / "README.md").write_text(render(summary))
    contract.write(root, folder, summary)
    write_index(snapshots_dir)
    write_readme_pointer(root / "README.md", snapshots_dir)
    return folder


def load_summaries(snapshots_dir: Path) -> list[dict[str, Any]]:
    """Every snapshot's summary, newest first."""
    found = [json.loads(p.read_text()) for p in snapshots_dir.glob("*/summary.json")]
    return sorted(found, key=lambda s: s["snapshot"]["created_at"], reverse=True)


def write_index(snapshots_dir: Path) -> None:
    lines = ["# Snapshots", "", ("Dated runs, newest first. Each folder has a readable README, "
             "the full `report.json` and the hub's `summary.json`."), "",
             "| Snapshot | Contenders | Items | Headline |", "|---|---:|---:|---|"]
    for s in load_summaries(snapshots_dir):
        sid = s["snapshot"]["id"]
        lines.append(f"| [{sid}]({sid}/) | {len(s['contenders'])} | {s['data']['items']} | "
                     f"{s['headline']} |")
    (snapshots_dir / "README.md").write_text("\n".join(lines) + "\n")


def replace_block(text: str, block: str, start: str = START, end: str = END) -> str:
    """Swap the text between markers, or append the marked block if the markers are absent."""
    wrapped = f"{start}\n{block}\n{end}"
    if start in text and end in text:
        return re.sub(re.escape(start) + r".*?" + re.escape(end), lambda _: wrapped, text,
                      flags=re.DOTALL)
    return f"{text.rstrip()}\n\n{wrapped}\n"


def write_readme_pointer(readme: Path, snapshots_dir: Path) -> None:
    summaries = [s for s in load_summaries(snapshots_dir) if not s["mock"]] or \
        load_summaries(snapshots_dir)
    if not summaries or not readme.exists():
        return
    s = summaries[0]
    rel = snapshots_dir.relative_to(readme.parent).as_posix()
    date = s["snapshot"]["created_at"][:10]
    block = (f"**[{date} snapshot]({rel}/{s['snapshot']['id']}/)**: {s['headline']}\n\n"
             f"All runs: [`{rel}/`]({rel}/).")
    readme.write_text(replace_block(readme.read_text(), block))
