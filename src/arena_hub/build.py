"""Build the hub: fetch every arena's latest summary, validate it, render one page and a README table.

    uv run hub build            # fetch summaries from GitHub (what CI does)
    uv run hub build --local    # read sibling checkouts listed in arenas.yaml
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from arena_kit import contract
from arena_kit.snapshot import fmt_quality, ms, replace_block, usd

ROOT = Path(__file__).resolve().parents[2]
START, END = "<!-- arenas:start -->", "<!-- arenas:end -->"

OUTCOME = {"changed": "Changed in production", "kept": "Kept current setup",
           "pending": "Decision pending", "not-applicable": "No product decision"}
LABELS = {"hand-written": "Hand-written labels", "production": "Production labels",
          "reviewed": "Reviewed labels", "llm-judge": "LLM-judge labels",
          "simulated": "Simulated data", "mixed": "Mixed labels"}
VERDICT_ORDER = {"pick": 0, "tied": 1, "baseline": 2, "other": 3, "worse": 4, "excluded": 5}


@dataclass
class Entry:
    repo: str
    path: str
    summary: dict[str, Any] | None
    problem: str | None = None


def load_registry(path: Path) -> list[dict[str, Any]]:
    return yaml.safe_load(path.read_text())["arenas"]


def fetch(arena: dict[str, Any], *, local: bool, http: httpx.Client) -> list[Entry]:
    entries = []
    for rel in arena.get("summaries", ["snapshots/latest.json"]):
        try:
            if local:
                text = (ROOT / arena["local"] / rel).read_text()
            else:
                branch = arena.get("branch", "main")
                url = f"https://raw.githubusercontent.com/{arena['repo']}/{branch}/{rel}"
                response = http.get(url)
                response.raise_for_status()
                text = response.text
            summary = json.loads(text)
            contract.validate(summary)
            problem = "mock run" if summary["mock"] else None
            entries.append(Entry(arena["repo"], rel, None if problem else summary, problem))
        except (OSError, httpx.HTTPError, json.JSONDecodeError, contract.ContractError) as error:
            entries.append(Entry(arena["repo"], rel, None, f"{type(error).__name__}: {error}"))
    return entries


def inline(text: str) -> str:
    """The tiny bit of markdown headlines use: **bold** and `code`."""
    out = html.escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", out)


def snapshot_url(s: dict[str, Any]) -> str:
    return f"{s['arena']['repo'].rstrip('/')}/tree/main/{s['snapshot']['path']}"


def title(s: dict[str, Any]) -> str:
    t = s["arena"].get("title") or s["arena"]["name"]
    return f"{t}: {s['arena']['study']}" if s["arena"].get("study") else t


def ranked(s: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    higher = s["metric"]["higher_is_better"]
    rows = sorted(s["contenders"], key=lambda c: (
        VERDICT_ORDER.get(c["verdict"], 9),
        -(c["quality"] or 0) if higher else (c["quality"] or 0)))
    return rows[:limit]


def card(s: dict[str, Any]) -> str:
    m = s["metric"]
    rows = "".join(
        f"<tr class='v-{c['verdict']}'><td>{html.escape(c['name'])}"
        f"{' <span class=tag>pick</span>' if c['verdict'] == 'pick' else ''}</td>"
        f"<td>{fmt_quality(c['quality'], m)}</td><td>{ms(c['p50_ms'])}</td>"
        f"<td>{usd(c['cost_per_1k_usd'])}</td></tr>"
        for c in ranked(s))
    more = len(s["contenders"]) - len(ranked(s))
    outcome = s["outcome"]
    outcome_text = html.escape(OUTCOME[outcome["status"]])
    if outcome.get("evidence_url"):
        outcome_text = f"<a href='{html.escape(outcome['evidence_url'])}'>{outcome_text}</a>"
    note = f"<p class=note>{inline(outcome['note'])}</p>" if outcome.get("note") else ""
    labels = s["data"]["labels"]
    return f"""
<article>
  <header>
    <h2><a href="{html.escape(s['arena']['repo'])}">{html.escape(title(s))}</a></h2>
    <time datetime="{s['snapshot']['created_at']}">{s['snapshot']['created_at'][:10]}</time>
  </header>
  <p class=question>{inline(s['arena']['question'])}</p>
  <p class=answer>{inline(s['headline'])}</p>
  <table>
    <thead><tr><th>Contender</th><th>{html.escape(m['label'])}</th><th>p50</th>
    <th>Cost / 1k</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  {f"<p class=more>+ {more} more in the snapshot</p>" if more > 0 else ""}
  <ul class=facts>
    <li title="{html.escape(labels.get('note', ''))}">{html.escape(LABELS[labels['source']])}</li>
    <li>{s['data']['items']:,} items</li>
    <li class="outcome o-{outcome['status']}">{outcome_text}</li>
  </ul>
  {note}
  <footer><a href="{html.escape(snapshot_url(s))}">Full snapshot →</a>
  <a href="{html.escape(s['arena']['repo'])}">Repo →</a></footer>
</article>"""


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Arenas</title>
<meta name="description" content="Engineering decisions from a real AI product, each settled by a reproducible measurement of quality, latency and cost.">
<style>
:root {{
  --bg: #fbfaf8; --surface: #ffffff; --text: #1d1c1a; --muted: #6b6862; --line: #e6e2db;
  --accent: #1f6f5c; --accent-soft: #e3f1ec; --warn: #8a5a00; --warn-soft: #f8eedb;
  --bad: #9b3b32;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg: #151514; --surface: #1d1d1b; --text: #ecebe7; --muted: #a09d96; --line: #33322f;
    --accent: #6cc3a8; --accent-soft: #1d332c; --warn: #e3b35c; --warn-soft: #362c18;
    --bad: #e08a80;
  }}
}}
:root[data-theme="dark"] {{
  --bg: #151514; --surface: #1d1d1b; --text: #ecebe7; --muted: #a09d96; --line: #33322f;
  --accent: #6cc3a8; --accent-soft: #1d332c; --warn: #e3b35c; --warn-soft: #362c18;
  --bad: #e08a80;
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--text);
  font: 16px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 1080px; margin: 0 auto; padding: 56px 16px 72px; }}
a {{ color: var(--accent); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
.intro h1 {{ font-size: 2rem; letter-spacing: -0.02em; margin: 0 0 12px; }}
.intro p {{ max-width: 68ch; color: var(--muted); margin: 0 0 12px; }}
.rules {{ display: flex; flex-wrap: wrap; gap: 8px; padding: 0; margin: 20px 0 40px;
  list-style: none; }}
.rules li {{ border: 1px solid var(--line); border-radius: 999px; padding: 4px 12px;
  font-size: 0.85rem; color: var(--muted); background: var(--surface); }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 480px), 1fr));
  gap: 20px; }}
article {{ background: var(--surface); border: 1px solid var(--line); border-radius: 14px;
  padding: 22px; display: flex; flex-direction: column; gap: 12px; min-width: 0; }}
article header {{ display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }}
h2 {{ font-size: 1.05rem; margin: 0; }}
time {{ color: var(--muted); font-size: 0.85rem; font-variant-numeric: tabular-nums;
  white-space: nowrap; }}
.question {{ font-size: 1.15rem; font-weight: 600; line-height: 1.35; margin: 0; }}
.answer {{ margin: 0; border-left: 3px solid var(--accent); padding-left: 12px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem;
  font-variant-numeric: tabular-nums; }}
th, td {{ text-align: right; padding: 6px 4px; border-bottom: 1px solid var(--line); }}
th:first-child, td:first-child {{ text-align: left; overflow-wrap: anywhere; }}
th {{ color: var(--muted); font-weight: 500; }}
tr.v-pick td {{ font-weight: 600; }}
tr.v-worse td, tr.v-excluded td {{ color: var(--muted); }}
.tag {{ background: var(--accent-soft); color: var(--accent); border-radius: 6px;
  padding: 1px 6px; font-size: 0.75rem; font-weight: 600; margin-left: 4px; }}
.more, .note {{ color: var(--muted); font-size: 0.85rem; margin: 0; }}
.facts {{ display: flex; flex-wrap: wrap; gap: 6px; list-style: none; padding: 0; margin: 0;
  font-size: 0.8rem; }}
.facts li {{ border: 1px solid var(--line); border-radius: 6px; padding: 2px 8px;
  color: var(--muted); }}
.facts .o-changed {{ background: var(--accent-soft); color: var(--accent); border-color: transparent; }}
.facts .o-pending {{ background: var(--warn-soft); color: var(--warn); border-color: transparent; }}
article footer {{ margin-top: auto; display: flex; gap: 16px; font-size: 0.9rem; }}
.missing {{ margin-top: 32px; color: var(--muted); font-size: 0.85rem; }}
.missing code {{ color: var(--bad); }}
code {{ font: 0.85em ui-monospace, SFMono-Regular, Menlo, monospace; }}
.foot {{ margin-top: 48px; color: var(--muted); font-size: 0.85rem; }}
</style>
</head>
<body>
<main>
  <section class=intro>
    <h1>Arenas</h1>
    <p>Each arena takes one real engineering decision from
    <a href="https://github.com/SeijiMatsumoto/enable-ai">enable-ai</a>, an AI-engineering news
    product, and settles it by measurement: several options behind one interface, scored on the
    same data for quality, latency and cost together.</p>
    <p>Picks come from fixed rules and paired significance tests, not from an LLM or a hunch, so
    the same report always gives the same answer. Every card says where its labels came from.</p>
  </section>
  <ul class=rules>
    <li>Real product decisions</li><li>Quality × latency × cost</li>
    <li>Paired tests, fixed rules</li><li>Honest label sources</li><li>Dated, reproducible snapshots</li>
  </ul>
  <section class=grid>{cards}</section>
  {missing}
  <p class=foot>Built {built} from each arena's <code>snapshots/latest.json</code>
  (<a href="https://github.com/SeijiMatsumoto/arenas/blob/main/kit/src/arena_kit/summary.schema.json">arena-summary/1</a>).
  Source: <a href="https://github.com/SeijiMatsumoto/arenas">SeijiMatsumoto/arenas</a>.</p>
</main>
</body>
</html>
"""


def render_page(summaries: list[dict[str, Any]], problems: list[Entry], built: str) -> str:
    missing = ""
    if problems:
        items = "".join(f"<li>{html.escape(p.repo)}: <code>{html.escape(p.problem or '')}</code></li>"
                        for p in problems)
        missing = f"<details class=missing><summary>{len(problems)} not shown</summary><ul>{items}</ul></details>"
    return PAGE.format(cards="".join(card(s) for s in summaries), missing=missing, built=built)


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_table(summaries: list[dict[str, Any]]) -> str:
    lines = ["| Arena | Question | Finding | Labels | Outcome |", "|---|---|---|---|---|"]
    for s in summaries:
        outcome = OUTCOME[s["outcome"]["status"]]
        if s["outcome"].get("evidence_url"):
            outcome = f"[{outcome}]({s['outcome']['evidence_url']})"
        lines.append(
            f"| [{cell(title(s))}]({s['arena']['repo']}) | {cell(s['arena']['question'])} | "
            f"{cell(s['headline'])} [→]({snapshot_url(s)}) | "
            f"{LABELS[s['data']['labels']['source']]} | {outcome} |")
    return "\n".join(lines)


def build(*, local: bool, out: Path, readme: Path | None, registry: Path) -> list[Entry]:
    with httpx.Client(timeout=30, follow_redirects=True) as http:
        entries = [e for a in load_registry(registry) for e in fetch(a, local=local, http=http)]
    shown = sorted((e.summary for e in entries if e.summary),
                   key=lambda s: s["snapshot"]["created_at"], reverse=True)
    problems = [e for e in entries if e.problem]
    out.mkdir(parents=True, exist_ok=True)
    built = datetime.now(UTC).strftime("%Y-%m-%d")
    (out / "index.html").write_text(render_page(shown, problems, built))
    (out / "summaries.json").write_text(json.dumps(shown, indent=1))
    if readme:
        readme.write_text(replace_block(readme.read_text(), render_table(shown), START, END))
    return entries


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="hub")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("build", help="render site/index.html from every arena's latest summary")
    p.add_argument("--local", action="store_true", help="read sibling checkouts, not GitHub")
    p.add_argument("--out", type=Path, default=ROOT / "site")
    p.add_argument("--readme", action="store_true", help="also update the table in README.md")
    p.add_argument("--strict", action="store_true", help="exit 1 if any arena is missing")
    args = parser.parse_args(argv)
    entries = build(local=args.local, out=args.out,
                    readme=ROOT / "README.md" if args.readme else None,
                    registry=ROOT / "arenas.yaml")
    for e in entries:
        status = "ok" if e.summary else f"skipped ({e.problem})"
        print(f"{e.repo}/{e.path}: {status}")
    if args.strict and any(e.problem for e in entries):
        sys.exit(1)
